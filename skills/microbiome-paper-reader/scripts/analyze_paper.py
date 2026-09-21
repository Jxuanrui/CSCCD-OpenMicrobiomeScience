#!/usr/bin/env python3
"""
analyze_paper.py — 7 模块并行精读 Agent

并发架构：
  Module 1/2/3 → 并行执行（concurrent.futures）
  Module 4     → 等待 M2+M3 完成后运行（论证逻辑链）
  Module 5/6   → 并行执行（不依赖 M4）
  Module 7     → 最后运行（整合所有结果，入库）

支持模型：
  --model claude        （默认，质量最高，成本最高）
  --model gemini-flash  （Gemini 2.5 Flash，推荐用于 tier2/disease/method）
  --model gemini-pro    （Gemini 2.5 Pro，推荐用于 tier1 精读）
  --model codex         （OpenAI o4-mini，中等成本）

Usage:
    python3 analyze_paper.py <PMID> [--raw path/to/{pmid}_raw.json] [--model gemini-flash]
    python3 analyze_paper.py 39367251 --model gemini-flash
"""

import json
import os
import sys
import argparse
import concurrent.futures
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

# ─── 环境变量 ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")

# ─── 统一 LLM 调用入口 ─────────────────────────────────────────────────────────

# 全局模型选择（由 main() 设置）
_ACTIVE_MODEL = "claude"

def set_model(model: str):
    global _ACTIVE_MODEL
    _ACTIVE_MODEL = model


def call_llm(system_prompt: str, user_content: str) -> str:
    """统一 LLM 调用，根据 _ACTIVE_MODEL 路由到不同后端。"""
    if _ACTIVE_MODEL.startswith("gemini"):
        return _call_gemini(system_prompt, user_content, _ACTIVE_MODEL)
    elif _ACTIVE_MODEL == "codex":
        return _call_openai(system_prompt, user_content, "o4-mini")
    else:
        return _call_claude(system_prompt, user_content)


def _call_claude(system_prompt: str, user_content: str,
                 model: str = "claude-sonnet-4-6") -> str:
    """Claude API（Anthropic SDK 优先，claude CLI 兜底）。"""
    try:
        import anthropic
        kwargs = {"api_key": ANTHROPIC_API_KEY}
        if ANTHROPIC_BASE_URL:
            kwargs["base_url"] = ANTHROPIC_BASE_URL
        client = anthropic.Anthropic(**kwargs)
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return msg.content[0].text
    except ImportError:
        pass
    except Exception as e:
        print(f"[analyze] Claude SDK error: {e}", file=sys.stderr)

    # 回退 claude CLI
    try:
        full_prompt = f"<system>\n{system_prompt}\n</system>\n\n{user_content}"
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        print(f"[analyze] Claude CLI error: {e}", file=sys.stderr)

    return "[ERROR: Claude unavailable]"


def _call_gemini(system_prompt: str, user_content: str, model_key: str) -> str:
    """Gemini API（google-generativeai SDK）。"""
    model_map = {
        "gemini-flash": "gemini-2.5-flash",
        "gemini-pro":   "gemini-2.5-pro",
        "gemini":       "gemini-2.5-flash",
    }
    model_id = model_map.get(model_key, "gemini-2.5-flash")

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=model_id,
            system_instruction=system_prompt,
        )
        response = model.generate_content(
            user_content,
            generation_config={"max_output_tokens": 4096, "temperature": 0.1},
        )
        return response.text
    except ImportError:
        print("[analyze] google-generativeai not installed. Run: pip install google-generativeai", file=sys.stderr)
    except Exception as e:
        print(f"[analyze] Gemini error: {e}", file=sys.stderr)

    return "[ERROR: Gemini unavailable]"


def _call_openai(system_prompt: str, user_content: str, model: str = "o4-mini") -> str:
    """OpenAI API（openai SDK，支持 Codex/o4-mini）。"""
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
        )
        return response.choices[0].message.content
    except ImportError:
        print("[analyze] openai not installed. Run: pip install openai", file=sys.stderr)
    except Exception as e:
        print(f"[analyze] OpenAI error: {e}", file=sys.stderr)

    return "[ERROR: OpenAI unavailable]"


# ─── 段落预处理 ────────────────────────────────────────────────────────────────

def prepare_context(raw: dict, max_chars: int = 40000) -> str:
    """将 passages 整理为结构化全文上下文，控制长度。"""
    passages = raw.get("passages", [])
    meta     = raw.get("meta", {})

    # 按类型优先级排序取用：abstract > title > paragraph > fig_caption > ref
    priority = {"abstract": 0, "front": 1, "title": 1, "title_1": 2, "title_2": 2,
                "paragraph": 3, "fig_caption": 4, "fig_title_caption": 4,
                "footnote": 5, "ref": 6}

    sorted_p = sorted(passages, key=lambda p: priority.get(p.get("type", "body"), 3))

    lines = [
        f"Title: {meta.get('title', '')}",
        f"Journal: {meta.get('journal', '')} | Year: {meta.get('year', '')}",
        f"DOI: {meta.get('doi', '')}",
        f"Authors: {meta.get('authors', '')}",
        "---",
    ]
    total = sum(len(l) for l in lines)

    for p in sorted_p:
        ptype = p.get("type", "body")
        text  = p.get("text", "").strip()
        if not text or ptype == "ref":      # 跳过参考文献段落（太长）
            continue
        entry = f"[{ptype.upper()}] {text}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    return "\n\n".join(lines)


# ─── 7 个分析模块 ──────────────────────────────────────────────────────────────

BIOMEDICAL_CONTEXT = """你是一位专注于肠道微生物组研究的资深科学家，同时熟悉生物医学论文的论证逻辑和实验设计。
分析结果要精炼、准确、有参考价值，避免泛泛而谈。用中文输出，专业术语保留英文。"""


def module_1_positioning(context: str, pmid: str) -> str:
    """M1：定位与背景"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""分析以下论文（PMID: {pmid}），完成「定位与背景」分析。

{context}

请输出以下结构（严格按格式）：

## M1：定位与背景

### 研究类型
[从以下选择并说明原因: 机制研究 / 临床队列研究 / 工具/方法开发 / 综述 / 干预研究 / 多组学整合研究]

### 科学问题
[一句话：作者要解决什么问题？提出了什么假说？]

### 领域贡献定位
[填补了什么具体空白？与已有研究的核心区别是什么？2-3句话]

### 影响力信号
- 期刊：[名称] | 分区：[tier1/2/3]
- 研究规模：[样本量/数据规模]
- 核心创新点：[1-2个最关键的创新]"""

    return call_llm(system, prompt)


def module_2_data(context: str, pmid: str) -> str:
    """M2：数据与队列描述"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""分析以下论文（PMID: {pmid}），完成「数据与队列」分析。

{context}

请输出以下结构：

## M2：数据与队列

### 样本描述
| 属性 | 内容 |
|------|------|
| 队列来源 | |
| 总样本量 | |
| 分组 | |
| 入组标准 | |
| 排除标准 | |
| 研究设计 | [横断面/纵向/干预/多中心] |

### 测序与检测类型
[16S / 宏基因组 / 代谢组 / 转录组 / 其他，注明测序深度/平台]

### 验证队列
[是否有独立验证队列？规模？来源？若无则注明]

### 数据质量评级
[A（大队列+外部验证）/ B（中等规模，内部验证）/ C（小样本，无验证）]
理由：[一句话说明评级依据]"""

    return call_llm(system, prompt)


def module_3_experiments(context: str, pmid: str) -> str:
    """M3：实验设计解构"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""分析以下论文（PMID: {pmid}），完成「实验设计解构」。

{context}

请列出论文中的所有实验（包括：计算分析、体外细胞实验、动物模型、临床验证、多组学整合等），
每个实验用以下格式描述：

## M3：实验设计解构

### 实验列表

**实验 1：[实验名称/类型]**
- 实验类型：[计算分析 / 体外实验 / 动物模型 / 临床队列分析 / 多组学整合]
- 目的：[这个实验要回答什么具体问题]
- 设计：[具体方法、对照、关键参数]
- 核心结果：[一句话：得到了什么结论]
- 支撑图表：[对应哪张 Figure/Table]

**实验 2：[...]**
[重复上述格式]

（列出所有主要实验，通常 3-6 个）

### 实验层次评估
- 最高证据层次：[描述性关联 / 统计建模 / 机制体外验证 / 动物因果验证 / 临床干预验证]
- 核心实验：[哪个实验是整篇论文最关键的支柱？去掉它论证就崩溃]"""

    return call_llm(system, prompt)


def module_4_argumentation(context: str, m2_result: str, m3_result: str, pmid: str) -> str:
    """M4：论证逻辑链（依赖 M2+M3）"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""基于以下信息，分析论文（PMID: {pmid}）的完整论证逻辑链。

【论文全文上下文】
{context[:15000]}

【M2 数据与队列分析结果】
{m2_result}

【M3 实验设计解构结果】
{m3_result}

请构建完整的论证逻辑链：

## M4：论证逻辑链

### 核心科学假说
[作者最终要证明的核心主张，一句话]

### 论证推进路径

```
[起点：科学问题/假说]
         │
         ▼ 用什么数据/实验
[实验1名称] → [中间结论1：得到了什么发现]
         │
         │ ← 为什么需要实验2？（实验1的不足/引出的新问题）
         ▼
[实验2名称] → [中间结论2]
         │
         │ ← 为什么需要实验3？
         ▼
[实验3名称] → [中间结论3]
         │
         ▼
[最终结论：作者的核心主张]
```

### 关键逻辑节点分析
- **论证支柱**：哪个实验是不可缺少的？去掉它论证为何崩溃？
- **逻辑跳跃**：论证链中最薄弱的跳跃是哪里？（哪步从数据到结论跨越最大）
- **因果强度**：最终结论的因果推断强度如何？[描述性关联 / 统计因果 / 实验因果]

### 与同类研究的论证差异
[与你了解的同类研究相比，这篇文章的论证路径有何独特之处？]"""

    return call_llm(system, prompt)


def module_5_writing_craft(context: str, pmid: str) -> str:
    """M5：写作工艺分析"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""分析以下论文（PMID: {pmid}）的写作工艺，帮助研究者学习如何构建高水平生物医学论文。

{context[:20000]}

## M5：写作工艺分析

### Introduction 论证结构（6步公式）
| 步骤 | 内容 | 评价 |
|------|------|------|
| Stakes（为什么重要）| | [有力/薄弱] |
| Gap（现有研究的不足）| | [结构性不足/量化不足] |
| Key Abstraction（核心概念）| | [有命名/无命名] |
| Design Intuition（方法直觉）| | |
| Contributions（贡献列表）| | [陈述具体数字/仅描述方法] |
| Results Preview（结果预览）| | [有具体数字/无] |

### 最可借鉴的写作技巧
1. [具体技巧，来自原文的证据]
2. [具体技巧]

### 可直接迁移到自己论文的表达模式
[列出 2-3 个具体的句式或段落结构]"""

    return call_llm(system, prompt)


def module_6_limitations(context: str, pmid: str) -> str:
    """M6：局限性与质疑预判"""
    system = BIOMEDICAL_CONTEXT
    prompt = f"""分析以下论文（PMID: {pmid}）的局限性，并预判审稿人可能的质疑。

{context[:20000]}

## M6：局限性与质疑预判

### 作者自述局限性
[从论文 Discussion 中提取，逐条列出]

### 补充局限性（作者未提及）
[你认为还存在哪些方法学/设计层面的局限]

### 审稿人最可能质疑的 3 个点
1. **[质疑类型]**：[具体质疑内容] → [建议的应对策略]
2. **[质疑类型]**：[具体质疑内容] → [建议的应对策略]
3. **[质疑类型]**：[具体质疑内容] → [建议的应对策略]

### 论证强度综合评级
- 因果链完整性：[✅ 有动物/FMT 验证 / ⚠️ 仅统计关联 / ❌ 纯描述]
- 外部可重复性：[✅ 多中心/独立验证 / ⚠️ 内部交叉验证 / ❌ 无验证]
- 混杂因素控制：[✅ 多变量模型 / ⚠️ 部分控制 / ❌ 未控制]
- 综合证据等级：[Level 1-4，说明依据]"""

    return call_llm(system, prompt)


def module_7_index(raw: dict, results: dict, pmid: str, knowledge_dir: str = "knowledge") -> dict:
    """M7：知识图谱入库（生成节点元数据，type 从文件系统实时派生）"""
    meta = raw.get("meta", {})
    from pathlib import Path

    # 实时判断：knowledge/papers/ 下有无对应代码节点
    papers_dir = Path(knowledge_dir) / "papers"
    has_code = (
        (papers_dir / f"pmid_{pmid}.json").exists() or
        any(papers_dir.glob(f"*{pmid}*.json"))
    )

    node = {
        "pmid":              pmid,
        "has_deepread":      True,
        "has_code":          has_code,
        "title":             meta.get("title", ""),
        "journal":           meta.get("journal", ""),
        "year":              meta.get("year", ""),
        "doi":               meta.get("doi", ""),
        "source":            raw.get("source", ""),
        "fulltext":          raw.get("fulltext_available", False),
        "indexed_at":        datetime.now().strftime("%Y-%m-%d"),
        "modules_complete":  list(results.keys()),
    }

    return node


# ─── 主编排器 ──────────────────────────────────────────────────────────────────

def run_analysis(pmid: str, raw: dict, out_dir: Path) -> dict:
    context = prepare_context(raw)
    print(f"[analyze] Context prepared: {len(context)} chars", file=sys.stderr)

    results = {}

    # 第一轮：M1 / M2 / M3 并行
    print("[analyze] Phase 1: Running M1, M2, M3 in parallel...", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f1 = ex.submit(module_1_positioning, context, pmid)
        f2 = ex.submit(module_2_data,        context, pmid)
        f3 = ex.submit(module_3_experiments, context, pmid)
        results["m1"] = f1.result()
        results["m2"] = f2.result()
        results["m3"] = f3.result()
    print("[analyze] Phase 1 done.", file=sys.stderr)

    # 第二轮：M4 依赖 M2+M3；M5/M6 并行（不依赖 M4）
    print("[analyze] Phase 2: Running M4 (depends M2+M3), M5+M6 in parallel...", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f4 = ex.submit(module_4_argumentation, context, results["m2"], results["m3"], pmid)
        f5 = ex.submit(module_5_writing_craft, context, pmid)
        f6 = ex.submit(module_6_limitations,   context, pmid)
        results["m4"] = f4.result()
        results["m5"] = f5.result()
        results["m6"] = f6.result()
    print("[analyze] Phase 2 done.", file=sys.stderr)

    # 第三轮：M7 整合入库
    print("[analyze] Phase 3: M7 indexing...", file=sys.stderr)
    node_meta = module_7_index(raw, results, pmid, knowledge_dir="knowledge")
    results["m7_node"] = node_meta

    return results


# ─── 输出整合 ──────────────────────────────────────────────────────────────────

def render_entry(pmid: str, raw: dict, results: dict) -> str:
    meta  = raw.get("meta", {})
    node  = results.get("m7_node", {})

    header = f"""# [{pmid}] {meta.get('title', 'Unknown Title')}

- **Journal**: {meta.get('journal', 'Unknown')} | **Year**: {meta.get('year', '?')}
- **DOI**: {meta.get('doi', 'N/A')}
- **Authors**: {meta.get('authors', 'N/A')}
- **Node Type**: {node.get('type', 'conceptual')} | **Source**: {raw.get('source', '?')}
- **Indexed**: {node.get('indexed_at', '')}

---
"""
    sections = [
        results.get("m1", ""),
        results.get("m2", ""),
        results.get("m3", ""),
        results.get("m4", ""),
        results.get("m5", ""),
        results.get("m6", ""),
    ]
    return header + "\n\n---\n\n".join(s for s in sections if s)


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pmid")
    parser.add_argument("--raw",   default="", help="Path to pre-fetched {pmid}_raw.json")
    parser.add_argument("--out",   default="knowledge/concepts/entries", help="Output directory")
    parser.add_argument("--graph", default="knowledge/graph/nodes.json", help="Graph index file")
    parser.add_argument("--model", default="claude",
                        choices=["claude", "gemini-flash", "gemini-pro", "codex"],
                        help="LLM backend (default: claude)")
    args = parser.parse_args()

    # 设置全局模型
    set_model(args.model)
    print(f"[analyze] Using model: {args.model}", file=sys.stderr)

    # 加载原始数据
    if args.raw:
        raw = json.loads(Path(args.raw).read_text())
    else:
        # 动态 import fetch_paper（同目录）
        sys.path.insert(0, str(Path(__file__).parent))
        from fetch_paper import fetch_paper
        raw = fetch_paper(args.pmid)

    # 运行分析
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = run_analysis(args.pmid, raw, out_dir)

    # 写精读 markdown
    entry_md = render_entry(args.pmid, raw, results)
    entry_path = out_dir / f"{args.pmid}.md"
    entry_path.write_text(entry_md, encoding="utf-8")
    print(f"[analyze] Entry written: {entry_path}", file=sys.stderr)

    # 更新 graph/nodes.json
    graph_path = Path(args.graph)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    nodes = []
    if graph_path.exists():
        nodes = json.loads(graph_path.read_text())
    # 去重
    nodes = [n for n in nodes if n.get("pmid") != args.pmid]
    nodes.append(results["m7_node"])
    graph_path.write_text(json.dumps(nodes, ensure_ascii=False, indent=2))
    print(f"[analyze] Graph index updated: {graph_path} ({len(nodes)} nodes)", file=sys.stderr)

    print(f"\n[analyze] Done. Output: {entry_path}", file=sys.stderr)
