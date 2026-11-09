"""kg 统一命令入口（阶段 1 W1.2）——薄转发层。

为什么不直接改七模块的 argparse：转发层零侵入，模块行为一个字节都不动——
这是 golden 等价闸的前提；参数逐模块上收到 Typer 留待阶段 2"参数归属全表"
落地时做，防止一次改动面过大。
K 线（K1-K5）刻意不进本入口：一次性方案红线"K 线不重构不重跑"。
"""
import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import typer

from kg_tool import __version__


def kg_root() -> Path:
    """仓根定位（W2.2，spec §四定案）：KG_ROOT 环境变量 > 自动探测。

    为什么允许环境变量覆盖：非 editable 打包安装时 parents[2] 不再指向仓
    ——数据腿（注册表锚定 ROOT）与 configs 的可移植性由 KG_ROOT 提供。
    """
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else Path(__file__).resolve().parents[2]


# 仓根定位：src/kg_tool/cli.py → parents[2]（与 06_tool 模块的 ROOT 约定同仓。
# pip editable 安装后文件仍在仓内，定位不破；打包安装的可移植性等阶段 2 配置化）
ROOT = kg_root()
TOOL_DIR = ROOT / "src" / "06_tool"

# 子命令名 -> (模块文件, 一句话用途)。十件=七模块+runner+explain+交付包生成器
MODULES = {
    "ingest": ("06_tool/ingest.py", "队列入库（--cohort 队列档案→观测层+sha 契约）"),
    "bridge": ("06_tool/name_bridge.py", "菌名桥（NCBITaxon↔MetaPhlAn）"),
    "subgraph": ("06_tool/subgraph.py", "疾病子图三档制（tier0/tier1/tier2/merge）"),
    "interact": ("06_tool/interact.py", "三桶对账（子图×gutMGene）"),
    "deepchain": ("06_tool/deepchain.py", "三跳机制链枚举（死端罚分剪枝）"),
    "inference": ("06_tool/inference.py", "链标注（观测层丰度+KO，只标不打分）"),
    "hypothesize": ("06_tool/hypothesize.py", "四票假设卡片（全机械）"),
    "run": ("06_tool/runner.py", "一键全流程（六步依赖序+断点续跑）"),
    "explain": ("kg_tool/explain.py", "假设卡片讲人话（只读解释层，默认模板/--llm）"),
    "report": ("kg_tool/report.py", "HTML 报告（只转印不计算）"),
    "recipes": ("kg_tool/recipes.py", "分析配方（推荐词条只转印）"),
    "mcp": ("kg_tool/mcp_server.py", "MCP 插座（stdio 本地，AI 助手直连）"),
    "delivery": ("06_tool/delivery_pack.py", "交付包生成（TOP10 详卡+前 N 简卡）"),
    "causal-marks": ("kg_tool/causal_marks.py",
                     "U1 因果语言标注 sidecar（冻结词表机械扫描，零 LLM 零排序变更）"),
    "graph-scores": ("kg_tool/graph_scores.py",
                     "U2 图结构分 sidecar（LCC 门禁先行，降级=描述性列，非排序分量）"),
    "shuffle-control": ("kg_tool/shuffle_control.py",
                        "U3 路径洗牌对照臂（基因列钉扎重配+四票重算+Δ 检验，"
                        "洗牌卡零落盘只产评估档）"),
    "proximity": ("kg_tool/proximity.py",
                  "U2b 菌→代谢物邻近度 sidecar（AMSPL+度匹配置换零模型，"
                  "两读法 TOP10/TOP100，只增不改）"),
    "hop1-shuffle": ("kg_tool/hop1_shuffle.py",
                     "U3a hop1 换跳洗牌（代谢物列全库重排+票③④联动重算+"
                     "Δ 检验，洗牌卡零落盘只产评估档）"),
    "degree-matched-null": ("kg_tool/degree_matched_null.py",
                            "U3b 度匹配随机链零模型（G_chain 逐位置度匹配"
                            "有放回抽样+四票重算+配对 Wilcoxon，随机链中间态"
                            "零落盘只产评估档）"),
    "gene-graft": ("kg_tool/gene_graft.py",
                   "②甲 子图基因层嫁接+proximity 激活（新目录隔离，"
                   "主子图零改动，U2 十列 degraded_mode=0）"),
<<<<<<< HEAD
    # ---- v0.4 四能力并入（roadmap 阶段一 1.1，评审 2026-11-08 批薄转发）----
=======
    # ---- v0.4 四能力并入（roadmap 阶段一 1.1，监工 2026-11-08 批薄转发）----
>>>>>>> origin/main
    "crosswalk": ("kg_tool/build_crosswalk.py",
                  "跨体系对照表生成（菌/疾病双侧桥接，child_of 只登记，"
                  "评估侧硬过滤）"),
    "timeslice": ("kg_tool/timeslice_eval.py",
                  "时间切片验证（KG@cutoff vs 库侧新文献考卷，cutoff 冻结=2023）"),
    "discover": ("kg_tool/open_discovery.py",
                 "开放发现三榜（Swanson ABC：病-代谢物/菌-基因/代谢物-通路，"
                 "候选≠已证关联）"),
    "counterfactual": ("kg_tool/counterfactual_eval.py",
                       "反事实证据贡献（TOP-N 卡逐边拔除→整管线重跑→排名位移）"),
}

app = typer.Typer(add_completion=False,
                  help="肠道菌群多层知识平台工具统一入口（阶段 1 薄转发层）")


def _load(module_file: str):
    """按文件路径加载 06_tool 模块。

    为什么不用 import：06_tool 目录名带连字符不是合法包名，
    仓内既有约定是路径加载/ sys.path 注入——沿约定，不引新机制。
    """
    spec = spec_from_file_location(module_file[:-3],
                                   ROOT / "src" / module_file)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _version_cb(value: bool):
    """--version 单源：版本号唯一出处=kg_tool.__version__（pyproject 动态读它）。"""
    if value:
        typer.echo(f"kg {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(
        None, "--version", callback=_version_cb, is_eager=True,
        help="显示版本号（单源 kg_tool.__version__）")):
    """kg —— 收拢各模块入口的统一命令（现二十四子命令）。"""


def _make_cmd(name: str, module_file: str, doc: str):
    """注册一个转发子命令。

    context_settings 两开关是关键：允许未知选项穿透——这样模块自己的
    argparse 原样收到全部参数（含它自己的 --help），转发层不解释任何参数。
    闭包晚绑定用默认参 _mf 钉死，防循环注册串模块。
    """
    @app.command(name=name, help=doc,
                 context_settings={"allow_extra_args": True,
                                   "ignore_unknown_options": True})
    def cmd(args: list[str] = typer.Argument(
            None, help="原样转发给模块 argparse（完整帮助：kg <命令> -- --help）"),
            _mf: str = module_file):
        mod = _load(_mf)
        raise typer.Exit(mod.main(args or []))
    cmd.__name__ = f"kg_{name}"


for _name, (_f, _doc) in MODULES.items():
    _make_cmd(_name, _f, _doc)


if __name__ == "__main__":
    sys.exit(app())
