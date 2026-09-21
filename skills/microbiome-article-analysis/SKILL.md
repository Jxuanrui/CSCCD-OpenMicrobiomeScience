---
name: microbiome-article-analysis
description: 核心桥梁 Skill。将已收录文献的分析方法适配到用户自己的数据，配置运行环境，输出可执行代码。输入 "list" 查看所有已装载知识节点。
---


# microbiome-article-analysis：文献方法复现与数据适配

## 职责定义

本 Skill 是连接"文献知识节点"与"用户数据分析"的唯一桥梁。

- **不负责**：解读文章创新点、推荐文献、写作润色
- **专注于**：把文献里验证过的分析方法，适配成可以在用户数据上运行的代码

## 知识库路径

- 文献节点：`knowledge/papers/pmid_*.json`
- 原始代码：`data/papers/{pmid}/source_code/`
- conda 环境：`data/papers/{pmid}/conda/environment.yml`
- MATLAB 依赖：`data/papers/{pmid}/matlab_requirements.md`（如有）

## 参数说明

`/microbiome-article-analysis` 接受以下三种输入：

| 输入 | 说明 | 示例 |
|------|------|------|
| `list` | 列出所有已装载的知识节点，附通俗解释 | `/microbiome-article-analysis list` |
| PMID 编号 | 直接指定文献，进入方法适配流程 | `/microbiome-article-analysis 33753486` |
| 自然语言描述 | 描述需求，自动匹配最相关的节点 | `/microbiome-article-analysis 我想做纵向菌群动态分析` |

## 当参数为 `list` 时的执行逻辑

**立即执行以下操作**，不需要询问用户任何问题：

1. 用 Bash 运行如下命令，读取所有知识节点并检查环境状态：

```bash
cd $KG_HOME/data/sources/MicrobeScholar && python3 -c "
import json, os, glob

papers_dir = 'knowledge/papers'
nodes = sorted(glob.glob(f'{papers_dir}/pmid_*.json'))

if not nodes:
    print('暂无已装载的知识节点。')
else:
    print(f'知识库共收录 {len(nodes)} 篇文献：\n')
    for path in nodes:
        with open(path) as f:
            d = json.load(f)
        pmid = d['pmid']
        code_flag = '✅ 含可运行代码' if d.get('has_runnable_code') else '📄 仅知识参考'
        types = ', '.join(d.get('analysis_types', []))
        mods = d.get('code_modules', [])

        # 检查 conda 环境是否存在
        env_path = f'data/papers/{pmid}/conda/env'
        env_status = '✅ 环境已就绪' if os.path.isdir(env_path) else '⚠ 环境未安装'

        # 检查源码是否存在
        src_path = f'data/papers/{pmid}/source_code'
        src_status = '✅ 代码已解压' if os.path.isdir(src_path) and os.listdir(src_path) else '⚠ 代码未解压'

        # 检查 README 是否存在
        readme_path = f'data/papers/{pmid}/README.md'
        readme_status = '✅' if os.path.isfile(readme_path) else '❌'

        print(f'[{pmid}] {d[\"journal\"]} {d[\"year\"]} | {code_flag}')
        print(f'  标题：{d[\"title\"]}')
        print(f'  一句话：{d[\"plain_description\"]}')
        print(f'  分析类型：{types}')
        print(f'  可复现模块数：{len(mods)} 个')
        print(f'  conda 环境：{env_status}  |  源码：{src_status}  |  README：{readme_status}')
        if mods:
            for m in mods:
                lang = m.get(\"language\", \"\")
                print(f'    • {m[\"module_id\"]}: {m[\"name\"]} [{lang}]')
        print()
"
```

2. 将输出格式化后展示给用户，末尾附上提示：
   > 输入 `/microbiome-article-analysis <PMID>` 开始适配某篇文献的方法到你的数据。

## 触发方式

### 方式1：用户直接触发

用户说类似以下内容时激活：
- "我想用 PMID:XXXXX 这篇文章的方法"
- "帮我用 [文章名] 的流程分析我的数据"
- "这篇文章的 Fig.3 是怎么做出来的，我想复现"

### 方式2：其他 Skill 内部转交

当 `microbiome-bioinformatics`、`microbiome-modeling`、`microbiome-multiomics` 等 Skill
判断知识库中存在与用户需求高度匹配的文献节点时，在回复中明确告知用户：
> "知识库中有一篇文章与你的需求高度匹配，可以调用 `/microbiome-article-analysis` 直接进行方法适配。"

## 执行流程

### Step 1：定位文献节点

```
用户提供 PMID 或关键词
  → 检索 knowledge/papers/pmid_*.json
  → 确认节点存在且 has_runnable_code = true
  → 读取 plain_description、analysis_types、code_modules
```

若节点不存在：告知用户该文献尚未入库，引导提交文献整理单。

### Step 2：理解用户数据

向用户确认以下信息（未提供则询问）：
- 数据类型：16S OTU/ASV 表 / 宏基因组 species 表 / 代谢组 / 其他
- 数据格式：文件路径、行列方向、列名规范
- 研究目的：复现原文某个图 / 将方法用于新数据 / 仅了解流程

### Step 3：环境配置

```
读取 data/papers/{pmid}/conda/environment.yml
  → 检查用户环境是否已有该 conda env
  → 若无：给出 conda env create 命令
  → MATLAB 类：给出 matlab_requirements.md 中的版本要求
```

### Step 4：代码适配输出

根据文献节点中的 `code_modules` 字段，找到对应脚本，修改：
- 输入路径 → 用户数据路径
- 分组变量名 → 用户元数据中的列名
- 输出路径 → 用户指定位置

输出适配后的代码片段，并说明：
- 需要手动修改的参数（标注 `# ← 修改这里`）
- 预期输出文件和图形

### Step 5：运行说明

- 给出完整的运行命令（conda activate → Rscript / python / matlab）
- 说明预计运行时间和内存需求（从节点 `runtime_notes` 字段读取）
- 给出常见报错的解决方向

## 输出格式

```
📄 文献：[标题] | PMID: XXXXX
📊 分析类型：[analysis_types]
🔧 运行环境：[conda env 名称 / MATLAB 版本]

─── 适配代码 ───────────────────────────────
[代码块，含注释，标注需修改参数]
────────────────────────────────────────────

▶ 运行命令：
  conda activate [env_name]
  Rscript [script_path]

📁 预期输出：[文件/图形列表]
⚠ 注意事项：[依赖、数据格式要求等]
```

## 边界声明

- 本 Skill 只处理**已入库文献**（`knowledge/papers/` 中有对应节点）
- 未入库文献请先通过文献整理单提交，由管理员处理后方可使用
- 代码执行结果的生物学解读，建议配合 `microbiome-bioinformatics` 或 `microbiome-multiomics`
