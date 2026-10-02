#!/usr/bin/env python3
"""Streamlit 图谱浏览器 v2（v1.3.0 + T1 C3 可视化升级）.

新增第 5 个 Tab：交互式网络图（pyvis 驱动，支持拖拽/缩放/悬停详情）。
其余 4 个 Tab 保持不变。

用法: streamlit run src/05_analysis/kg_browser.py --server.headless true --server.port 8765
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from graph_analysis import EDGES, NODES, load_graph, resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "data/merged/candidate_v2"
PREDS = CANDIDATE / "rotate_predictions.tsv"
METRICS = ROOT / "data/merged/graph_metrics.json"

st.set_page_config(page_title="肠道菌群知识图谱浏览器", layout="wide", page_icon="🦠")
st.title("🦠 肠道菌群知识图谱浏览器")
st.caption("证据分级：A=策展实验数据 · B=LLM抽取且≥2篇支持 · C=单篇待审 · predicted=模型假设（非实测证据）")

TABS = st.tabs(["单菌跨域扇出", "交互式网络图", "社区（Louvain）", "链接预测（假设）", "Tier-C 待审边"])

# ===== 颜色映射 =====
CATEGORY_COLORS = {
    "Microbe": "#4ECDC4", "Disease": "#FF6B6B", "Drug": "#45B7D1",
    "Metabolite": "#FFA07A", "Gene": "#98D8C8", "Pathway": "#DDA0DD",
    "Food": "#90EE90", "literature_only": "#D3D3D3",
}
TIER_SHAPES = {"A": "dot", "B": "dot", "C": "dot", "predicted": "diamond"}


def fanout(g, meta, nid, hops):
    import networkx as nx
    und = nx.Graph(g)
    dist = {nid: 0}
    frontier = [nid]
    for d in range(1, hops + 1):
        nxt = []
        for u in frontier:
            for v in und.neighbors(u):
                if v not in dist:
                    dist[v] = d
                    nxt.append(v)
        frontier = nxt
    rows = []
    for v, d in dist.items():
        if v == nid:
            continue
        direct = None
        if g.has_edge(nid, v):
            direct = next(iter(g[nid][v].values()))
        elif g.has_edge(v, nid):
            direct = next(iter(g[v][nid].values()))
        rows.append({"hop": d, "name": meta[v]["name"], "id": v,
                     "category": meta[v]["category"],
                     "predicate": direct["predicate"] if direct else "",
                     "tier": direct["tier"] if direct else "",
                     "pmids": direct["pmids"] if direct else "",
                     "confidence": direct["conf"] if direct else ""})
    return pd.DataFrame(rows)


# ===== Tab 0: 单菌跨域扇出（原有） =====
with TABS[0]:
    col1, col2, col3 = st.columns([3, 1, 1])
    q = col1.text_input("微生物名称或 ID", value="Faecalibacterium prausnitzii")
    hops = col2.slider("跳数", 1, 3, 2)
    if st.button("查询", type="primary"):
        g, meta, _ = load_graph()
        nid = resolve(q, meta)
        if not nid:
            st.error(f"未找到实体：{q}")
        else:
            df = fanout(g, meta, nid, hops)
            st.success(f"{meta[nid]['name']}（{nid}）1-{hops} 跳扇出 {len(df)} 个邻居")
            c1, c2 = st.columns(2)
            c1.metric("按类别", df.category.value_counts().to_dict().__str__()[1:-1].replace("'", ""))
            c2.metric("按跳数", df.hop.value_counts().sort_index().to_dict().__str__()[1:-1].replace("'", ""))
            cats = st.multiselect("类别过滤", sorted(df.category.unique()),
                                  default=[c for c in ["Disease", "Metabolite", "Drug", "Gene", "Pathway"] if c in set(df.category)])
            show = df[df.category.isin(cats)].sort_values(["hop", "category", "name"])
            st.dataframe(show, use_container_width=True, height=520)
            st.download_button("下载 TSV", show.to_csv(sep="\t", index=False).encode(),
                               f"fanout_{nid.split(':')[-1]}.tsv", "text/tab-separated-values")


# ===== Tab 1: 交互式网络图（C3 新增） =====
with TABS[1]:
    st.subheader("🔗 交互式网络图")
    st.caption("拖拽移动节点 · 滚轮缩放 · 悬停查看详情 · 双击聚焦")

    col_a, col_b, col_c = st.columns([2, 1, 1])
    center_entity = col_a.text_input("中心实体", value="Faecalibacterium prausnitzii", key="viz_center")
    viz_hops = col_b.slider("展开跳数", 1, 3, 1, key="viz_hops")
    max_nodes = col_c.slider("最大节点数", 20, 200, 50, key="viz_max")

    if st.button("生成网络图", type="primary", key="viz_btn"):
        g, meta, _ = load_graph()
        nid = resolve(center_entity, meta)
        if not nid:
            st.error(f"未找到实体：{center_entity}")
        else:
            df = fanout(g, meta, nid, viz_hops)
            df = df[df.hop <= viz_hops].head(max_nodes - 1)

            from pyvis.network import Network
            import networkx as nx

            net = Network(height="600px", width="100%", bgcolor="#1a1a2e",
                          font_color="white", filter_menu=True, select_menu=True)

            # 添加中心节点
            center_color = CATEGORY_COLORS.get(meta[nid]["category"], "#AAAAAA")
            net.add_node(nid, label=meta[nid]["name"], color=center_color, size=25,
                         title=f"ID: {nid}\n类别: {meta[nid]['category']}\n（中心节点）",
                         shape="star")

            # 添加邻居节点
            for _, row in df.iterrows():
                color = CATEGORY_COLORS.get(row["category"], "#AAAAAA")
                title = f"ID: {row['id']}\n类别: {row['category']}\n"
                if row.get("predicate"):
                    title += f"关系: {row['predicate']}\n"
                if row.get("tier"):
                    title += f"证据: Tier {row['tier']}\n"
                if row.get("pmids"):
                    title += f"PMID: {row['pmids'][:50]}"
                net.add_node(row["id"], label=row["name"], color=color,
                             size=15 if row["hop"] == 1 else 10, title=title)

            # 添加边
            for _, row in df.iterrows():
                if row.get("predicate"):
                    net.add_edge(nid, row["id"], label=row["predicate"],
                                 color="#666666" if row["hop"] > 1 else "#999999",
                                 title=f"{row['predicate']} (Tier {row.get('tier','')})")

            # 物理引擎配置
            net.set_options("""
            {
              "physics": {
                "barnesHut": {
                  "gravitationalConstant": -3000,
                  "centralGravity": 0.3,
                  "springLength": 100,
                  "springConstant": 0.04,
                  "damping": 0.09
                }
              },
              "interaction": {
                "hover": true,
                "tooltipDelay": 200,
                "navigationButtons": true,
                "keyboard": true
              }
            }
            """)

            # 渲染
            net.save_graph("/tmp/kg_network.html")
            with open("/tmp/kg_network.html", "r") as f:
                html = f.read()
            st.components.v1.html(html, height=620, scrolling=True)

            st.info(f"已渲染 {len(df)+1} 个节点 · 中心: {meta[nid]['name']} · {viz_hops} 跳扇出")

    # 图例
    st.markdown("**图例**：" + " · ".join(
        f"<span style='color:{c}'>●</span> {cat}" for cat, c in CATEGORY_COLORS.items()
    ), unsafe_allow_html=True)


# ===== Tab 2: 社区（原有） =====
with TABS[2]:
    if METRICS.exists():
        import json
        m = json.loads(METRICS.read_text(encoding="utf-8"))
        comms = m.get("louvain_communities_top15", [])
        pick = st.selectbox("选择社区", comms,
                            format_func=lambda c: f"社区{c['id']}（{c['size']}节点 · {c['categories']}")
        st.write("代表成员：")
        st.write(pick["top_members"])


# ===== Tab 3: 链接预测（原有，改读 candidate_v2） =====
with TABS[3]:
    if PREDS.exists():
        p = pd.read_csv(PREDS, sep="\t")
        st.caption(f"RotatE 链接预测 Top {len(p)}（evidence=predicted，仅作假设线索，不得作为已证实结论引用）")
        s = st.text_input("按菌名/病名过滤（子串）")
        show = p[p.apply(lambda r: s.lower() in str(r.to_dict()).lower(), axis=1)] if s else p
        st.dataframe(show, use_container_width=True, height=520)
    else:
        st.info("预测文件尚未生成。运行 rotate_embed.py 生成。")


# ===== Tab 4: Tier-C 待审边（原有） =====
with TABS[4]:
    review_path = ROOT / "data/staging/llm_v2_review.tsv"
    if review_path.exists():
        r = pd.read_csv(review_path, sep="\t")
        st.caption(f"Tier-C 单篇文献边 {len(r)} 条（待审，不参与下游结论）")
        s2 = st.text_input("过滤（子串）", key="review_filter")
        show2 = r[r.apply(lambda row: s2.lower() in str(row.to_dict()).lower(), axis=1)] if s2 else r
        cols = [c for c in ["pmid", "subject_name", "predicate", "object_name",
                            "confidence", "evidence", "flag_or_override"] if c in show2.columns]
        st.dataframe(show2[cols], use_container_width=True, height=520)
    else:
        st.info("待审文件尚未生成（Tier-C 边在 merge_qc 后产出）。")
