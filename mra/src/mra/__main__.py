"""mra 欢迎入口：python -m mra —— 项目介绍、执行链条、工具速查、环境自检。

设计约束（Ponytail）：零新依赖（纯 ANSI/标准库）；非 TTY、NO_COLOR、
MRA_NO_BANNER=1 或 --no-banner 时跳过动画只出静态文本，不污染脚本管道的
机器可读输出；自检项只做存在性检查（绝不打印密钥值），任何单项异常不致
命；退出码恒 0。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

BANNER = [
    r"███╗   ███╗ █████╗ ███████╗ ██████╗ ███████╗",
    r"████╗ ████║██╔══██╗╚══███╔╝██╔═══██╗██╔════╝",
    r"██╔████╔██║███████║  ███╔╝██║   ██║███████╗",
    r"██║╚██╔╝██║██╔══██║ ███╔╝ ██║   ██║╚════██║",
    r"██║ ╚═╝ ██║██║  ██║███████╗╚██████╔╝███████║",
    r"╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚══════╝",
]
TAGLINE = "Microbiome Research Agent · 菌群科研 Agent 工具仓"

INTRO = """\
mra 是模型可插拔、知识可审计、权限可控的肠道菌群科研 Agent 平台，
依附 Claude Code / ZCode 等终端 Agent 使用：Agent 出题，mra 受控执行。"""

CHAIN = """\
执行链条（数据只进不出统计门，治理贯穿全程）：
  队列数据契约(只读) ─→ R统计沙箱(秩残差偏Spearman+BH)
        │                     └─ 治理门: 资源限制+审计账本+统计审计
        ├─→ 知识图谱快照(证据分级tier/pmids) ─→ 命中定级
        ├─→ 文献速读(PubMed+LLM, 带缓存)  ─→ 语义检索(vecstore)
        └─→ 研究循环: LLM规划器每轮1个白名单动作 → 会话/发现/报告"""

ENTRIES = """\
常用入口（均在 mra/ 下 uv run）：
  python -m mra.research --question "..." --target "..."   # 自主研究会话(断点续跑 --resume)
  python -m mra.research.atlas --out var/research/atlas_x  # 全景扫描(过治理门)
  python -m mra.kg query --term "<菌名>" --hops 2          # 图谱查询(证据分级)
  python -m mra.research.litread --query "..." --question "..."  # 文献速读
  python -c "from mra.vecstore import query; print(query('...', 'kg_entities', k=8))"
  python -m mra.benchmark.microbiome_eval 2022             # 评测
  python -m mra                                           # 本欢迎页"""

# 256色渐变停靠点（深蓝→青→亮青），扫描动画沿列移动
_GRADIENT = (17, 18, 19, 24, 31, 38, 44, 50, 51, 86, 87, 117)


def _colorize(line: str, phase: int) -> str:
    out = []
    for col, ch in enumerate(line):
        color = _GRADIENT[(col + phase) % len(_GRADIENT)]
        out.append(f"\033[38;5;{color}m{ch}")
    return "".join(out) + "\033[0m"


def _play_banner(stream) -> None:
    for phase in range(0, len(_GRADIENT) * 2, 2):
        stream.write("\033[H\033[J" if phase == 0 else "\033[F" * len(BANNER))
        for line in BANNER:
            stream.write(_colorize(line, phase) + "\n")
        stream.flush()
        time.sleep(0.05)


def _check(label: str, ok: bool | None, note: str = "") -> str:
    mark = {True: "✓", False: "✗", None: "－"}[ok]  # None=不适用/未知
    return f"  {mark} {label}{('：' + note) if note else ''}"


def env_report() -> list[str]:
    lines = ["环境自检（只查有无，不显内容）："]

    rscript = os.environ.get("RSCRIPT_BIN", "")
    lines.append(_check("RSCRIPT_BIN（R 统计沙箱）", bool(rscript) and shutil.which(rscript) is not None,
                        rscript or "未设置——统计链路不可用"))
    merged = os.environ.get("KG_MERGED_DIR", "")
    lines.append(_check("KG_MERGED_DIR（图谱源目录）", bool(merged) and os.path.isdir(merged),
                        merged or "未设置——无法新建快照（已有快照仍可用）"))
    try:
        from .kg.snapshot import list_snapshots
        snaps = list_snapshots()
        lines.append(_check("图谱快照", bool(snaps),
                            f"{len(snaps)} 份，最新 {snaps[-1]['snapshot_id']}" if snaps else "无"))
    except Exception:
        lines.append(_check("图谱快照", None, "读取失败"))
    try:
        from .research import datasources as ds
        cfg = ds.load_config()
        lines.append(_check("队列数据契约", True,
                            f"{len(cfg.get('exposures', {}))} 暴露表 × "
                            f"{len(cfg.get('features', {}))} 特征表"))
    except Exception:
        lines.append(_check("队列数据契约", False, "缺 var/cohort_config.json"))
    for key, use in (("ARK_API_KEY", "LLM规划/文献速读"), ("GLM_API_KEY", "GLM兜底"),
                     ("NCBI_API_KEY", "PubMed限速豁免"), ("GITHUB_TOKEN", "仓库拉取")):
        lines.append(_check(f"{key}（{use}）", bool(os.environ.get(key))))
    from pathlib import Path
    vecstore = Path(__file__).resolve().parents[3] / "var" / "vecstore" / "lancedb"
    lines.append(_check("vecstore 语义索引", vecstore.is_dir()))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mra", description="mra 欢迎页：项目介绍+环境自检")
    parser.add_argument("--no-banner", action="store_true", help="跳过动画，直接输出静态文本")
    args = parser.parse_args(argv)

    animated = (sys.stdout.isatty() and not os.environ.get("NO_COLOR")
                and not os.environ.get("MRA_NO_BANNER") and not args.no_banner)
    if animated:
        _play_banner(sys.stdout)
        print("\033[1m" + TAGLINE + "\033[0m")
    else:
        print("\n".join(BANNER))
        print(TAGLINE)
    print()
    print(INTRO)
    print()
    print(CHAIN)
    print()
    print(ENTRIES)
    print()
    print("\n".join(env_report()))
    print("\n所有统计执行过治理门（资源限制+审计账本+统计审计）；伪相关守卫：常数列一律拒绝。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
