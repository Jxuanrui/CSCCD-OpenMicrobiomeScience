"""MCP 插座（阶段 3 轨D）——AI 助手直连 kg 工具的薄转发层。

五护栏（评审 W2.3 审+轨D 批准条件）的机器化：
1. **纯转发**：每个 tool 都是薄壳——调 runner/recipes/explain 的既有
   main/load，统计逻辑零沾染；
2. **stdio 本地**：transport 白名单只有 'stdio'（argparse choices 锁死，
   sse/streamable-http 传不进来）+ 源码红线 grep 禁
   socket/listen/bind/http/localhost（test_mcp_server.py 机器化）；
3. **不进 06_tool**：本文件住 kg_tool（接口层）；06_tool 主模块
   mcp/llm 的 **import 行**零命中（字样级不可行——llm_extracted 系 K3
   冻结数据字段名）；
4. **不动冻结 schema**：所有输出只转印既有 TSV/JSON 字段；
5. 依赖钉扎 mcp==2.3.0（B0 转正档 data/logs/mcp_sdk_survey_20261103.md）。

注：插座层故意不做"讲人话"——AI 客户端（DeepSeek/Claude 等）本身就是
语言模型；本层只负责把机械事实结构化地递过去。
"""
import argparse
import csv
import json
import sys
from pathlib import Path

from mcp.server import MCPServer

from kg_tool.cli import kg_root

ROOT = kg_root()  # 复用 KG_ROOT>自动探测（评审轨D审 P1-2：打包安装后 parents[2] 失效）

mcp = MCPServer("kg")


def _read_top(sg_dir: str, top_n: int) -> list[dict]:
    p = Path(sg_dir) / "top_hypotheses.tsv"
    with open(p, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return rows[:top_n]  # 沿既有排序，只截取不重排


@mcp.tool()
def list_diseases() -> str:
    """列出可用疾病档案 key（configs/diseases/）。"""
    return json.dumps(sorted(x.stem
                             for x in (ROOT / "configs" / "diseases").glob("*.json")),
                      ensure_ascii=False)


@mcp.tool()
def list_cohorts() -> str:
    """列出可用队列档案 key（configs/cohorts/）。"""
    return json.dumps(sorted(x.stem
                             for x in (ROOT / "configs" / "cohorts").glob("*.json")),
                      ensure_ascii=False)


@mcp.tool()
def get_top_hypotheses(subgraph_dir: str, top_n: int = 10) -> str:
    """转印 TOP 假设卡（沿既有排序；含三列披露与票①警示字段）。"""
    rows = _read_top(subgraph_dir, top_n)
    return json.dumps(rows, ensure_ascii=False)


@mcp.tool()
def get_analysis_recipes(disease: str) -> str:
    """转印分析配方（推荐词条+仓内证据路径；candidate=未实测）。"""
    from kg_tool import recipes
    return json.dumps(recipes.load(disease), ensure_ascii=False)


@mcp.tool()
def run_pipeline(disease: str, cohort: str, tier2_batch: str,
                 out_dir: str) -> str:
    """一键跑全流程（纯转发 runner：六步依赖序+checkpoint 断点续跑）。

    返回 run_state.json 摘要（转印，含每步产物指纹）。
    """
    sys.path.insert(0, str(ROOT / "src" / "06_tool"))
    import runner  # 06_tool 目录名带连字符不可包导入，沿仓内路径加载惯例
    rc = runner.main(["--disease", disease, "--cohort", cohort,
                      "--tier2-batch", tier2_batch, "--out-dir", out_dir])
    state = json.loads((Path(out_dir) / "run_state.json").read_text(
        encoding="utf-8"))
    state["runner_rc"] = rc
    return json.dumps(state, ensure_ascii=False)


@mcp.tool()
def run_crosswalk(mesh_snapshot_dir: str, out_dir: str) -> str:
    """生成跨体系对照表（薄转发 build_crosswalk；child_of 只登记）。"""
    from kg_tool import build_crosswalk
    rc = build_crosswalk.main(["--mesh-snapshot", mesh_snapshot_dir,
                               "--out-dir", out_dir])
    return json.dumps({"rc": rc})


@mcp.tool()
def run_timeslice(year_snapshot: str, mesh_tree: str, out_dir: str) -> str:
    """时间切片验证（cutoff=2023 冻结）。"""
    from kg_tool import timeslice_eval
    rc = timeslice_eval.main(["--year-snapshot", year_snapshot,
                              "--mesh-tree", mesh_tree, "--out", out_dir])
    return json.dumps({"rc": rc})


@mcp.tool()
def run_discover(gutmgene_t1: str, out_dir: str) -> str:
    """开放发现三榜（候选≠已证关联）。"""
    from kg_tool import open_discovery
    rc = open_discovery.main(["--gutmgene-t1", gutmgene_t1, "--out", out_dir])
    return json.dumps({"rc": rc})


@mcp.tool()
def run_counterfactual(work_dir: str, out_dir: str, top_n: int = 10) -> str:
    """反事实证据贡献（TOP-N 卡逐边拔除→排名位移）。"""
    from kg_tool import counterfactual_eval
    rc = counterfactual_eval.main(["--work", work_dir, "--out", out_dir,
                                   "--top-n", str(top_n)])
    return json.dumps({"rc": rc})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="kg MCP 插座（stdio 本地）")
    # 护栏②机器化：transport 白名单只有 stdio——sse/http 传不进来
    ap.add_argument("--transport", choices=["stdio"], default="stdio")
    args = ap.parse_args(argv)
    mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
