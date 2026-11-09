"""HTML 报告生成器（阶段 3 轨B）——只"转印"，绝不"计算"。

<<<<<<< HEAD
三钉（评审 W2.3/W3A 审口径）：
=======
三钉（监工 W2.3/W3A 审口径）：
>>>>>>> origin/main
1. 输入白名单=top_hypotheses.tsv + 三个 manifest——只读固化层产物；
2. 沿用既有排序——top 文件什么顺序就什么顺序渲染，禁重排；
3. 源码禁统计禁 LLM——本模块不得出现 statistics/sum(/len( 等任何计算
   或 LLM/联网调用；一切数字只能从 manifest 的 counts 原文转印
   （红线由 test_report.py 的源码 grep 门禁锁死，violation 即测试红）。
"""
import argparse
import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 输入白名单（三钉之一：只许读这些）
INPUT_WHITELIST = ("top_hypotheses.tsv", "hypothesis_manifest.json",
                   "chains_manifest.json", "interaction_manifest.json")


def load_cards(sg_dir: Path) -> list:
    with open(sg_dir / "top_hypotheses.tsv", encoding="utf-8",
              newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))  # 顺序=文件顺序


def render(sg_dir: Path, title: str) -> str:
    cards = load_cards(sg_dir)
    hm = json.loads((sg_dir / "hypothesis_manifest.json"
                     ).read_text(encoding="utf-8"))
    cm = json.loads((sg_dir / "chains_manifest.json"
                     ).read_text(encoding="utf-8"))
    im = json.loads((sg_dir / "interaction_manifest.json"
                     ).read_text(encoding="utf-8"))
    e = html.escape
    first = cards[0]
    # 空证对照断言法定嵌套位=red_lines.null_control_assert（轨B 审 P0-1：
    # 缺失即 fail-closed，与三列披露同口径——防"测试绿但真跑空"）
    nca = hm.get("red_lines", {}).get("null_control_assert")
    if nca is None:
        sys.exit("[report] manifest 缺 red_lines.null_control_assert"
                 "（fail-closed）")
    counts = hm.get("counts", {})

    rows_html = []
    for c in cards:
        rows_html.append(
            f"<tr><td>{e(c['microbe_name'])}</td>"
            f"<td>{e(c['metabolite_name'])}</td>"
            f"<td>{e(c['hop2_gene_name'])}</td>"
            f"<td>{e(c['hop3_pathway'])}</td>"
            f"<td class=num>{e(c['votes_total'])}</td>"
            f"<td class=num>{e(c['kg_coi_rate'])}</td>"
            f"<td class=num>{e(c['vote4_measurability'])}</td></tr>")
    table = ("<table><tr><th>菌</th><th>代谢物</th><th>端点基因</th>"
             "<th>通路</th><th>总票</th><th>步进验证率</th><th>可测性</th>"
             "</tr>" + "".join(rows_html) + "</table>")

    # 统计区：全部转印 manifest 原文（counts 键值逐条 escape 打印）
    stats_lines = [f"<li>{e(str(k))}: {e(str(v))}</li>"
                   for k, v in counts.items()]

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{e(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2em auto;max-width:980px;
color:#222}} h1{{font-size:1.4em}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #bbb;padding:6px 8px;font-size:0.9em;text-align:left}}
th{{background:#f2f2f2}} .num{{text-align:center}}
.disc{{background:#fff8e1;border-left:4px solid #f0a;padding:8px 12px;
font-size:0.85em}} .warn{{color:#a00;font-size:0.85em}}
footer{{margin-top:2em;font-size:0.8em;color:#666}}
</style></head><body>
<h1>{e(title)}</h1>
<p class=disc>披露：kg_version={e(first['kg_version'])}｜证据粒度=
{e(first['evidence_granularity'])}｜k5_gate_status=
{e(first['k5_gate_status'])}（三列随卡转印）</p>
<h2>TOP 假设（{e(str(hm.get('spec', '')))}）</h2>
{table}
<p class=warn>票①警示：{e(first['confound_warning'])}</p>
<h2>统计（转印 hypothesis_manifest.counts 原文，含 TOP 明细原文）</h2>
<ul>{''.join(stats_lines)}</ul>
<h2>上下游（转印 chains/interaction manifest）</h2>
<ul>
<li>机制链：{e(str(cm.get('counts', {})))}</li>
<li>对账报告：{e(str(im.get('counts', {})))}</li>
<li>空证对照：{e(str(nca))}（掏空外证后
证据驱动三票全零的机器断言）</li>
</ul>
<footer>本报告为固化层产物只读转印（零计算、零模型调用，纯机械抄录）；全量卡片见
hypothesis_cards.tsv（{e(str(counts.get('n_cards', '见 manifest')))} 张）与 top_hypotheses.tsv；
复现：kg run --disease &lt;病卡&gt; --cohort &lt;队列卡&gt; …；解释：kg explain。</footer>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HTML 报告（只转印不计算）")
    ap.add_argument("--subgraph-dir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="输出 .html")
    ap.add_argument("--title", default="机制假设卡片报告")
    args = ap.parse_args(argv)
    if not (args.subgraph_dir / "top_hypotheses.tsv").exists():
        sys.exit(f"[report] 缺 {args.subgraph_dir}/top_hypotheses.tsv"
                 f"（fail-closed）")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(args.subgraph_dir, args.title),
                        encoding="utf-8")
    print(f"[report] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
