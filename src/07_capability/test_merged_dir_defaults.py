#!/usr/bin/env python3
"""KG_MERGED_DIR 默认值一致性契约（监工路径规整 P0-1，2026-10-07）。

背景：5 个管线脚本曾默认根层 data/merged（v2 旧数据），与 graph_analysis/
lightrag_qa 的 candidate_v3 默认不一致——不设环境变量运行 neo4j_materialize
会用 v2 数据覆盖 v3 活库。本测试锁定：全仓所有 KG_MERGED_DIR 读取点的默认值
必须统一为 data/merged/candidate_v3，且 src 内不得再出现指向根层
data/merged 的裸默认或硬编码 TSV 路径。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
MRA = SRC.parent / "mra"
EXPECTED_DEFAULT = "data/merged/candidate_v3"

# 读取 KG_MERGED_DIR 的合法写法（environ.get / getenv 两种）
READERS = re.compile(
    r'(?:os\.environ\.get|_?os\.environ\.get|os\.getenv|_?os\.getenv)\(\s*["\']KG_MERGED_DIR["\']\s*,?\s*([^)]*)\)')

# `environ.get(...) or "default"` 变体（默认值在 or 右侧——sample_supp_bg 写法）
OR_DEFAULT = re.compile(
    r'(?:os\.environ\.get|_?os\.environ\.get|os\.getenv|_?os\.getenv)\(\s*["\']KG_MERGED_DIR["\']\s*\)\s*or\s*["\']([^"\']*)["\']')

# 根层 data/merged 下的管线数据文件（禁止 src 硬编码引用——一律走 KG_MERGED_DIR）
ROOT_MERGED_FILES = re.compile(r'["\']data/merged/(merged_nodes|merged_edges|relation_assertions|graph_metrics|link_predictions|snapshot_manifest)[^"\']*["\']')


def _scan_roots():
    roots = [SRC]
    if MRA.is_dir():
        roots.append(MRA / "src")  # mra 源码（不含 tests——测试见下）
        roots.append(MRA / "tests")
    return roots


def test_all_kg_merged_dir_defaults_point_to_candidate_v3():
    offenders = []
    for root in _scan_roots():
        for py in root.rglob("*.py"):
            text = py.read_text(errors="ignore")
            for m in READERS.finditer(text):
                default = m.group(1).strip().strip("'\"")
                if not default:
                    continue  # 空默认=强制显式设置，合法
                # 默认值可能是多行表达式（Path(__file__).../candidate_v3），取匹配后窗口判定
                window = text[m.start():m.end() + 200]
                if "candidate_v3" in default or "candidate_v3" in window:
                    continue
                offenders.append(f"{py}: 默认值 {default!r}")
            for m in OR_DEFAULT.finditer(text):
                default = m.group(1).strip()
                if not default:
                    continue
                if "candidate_v3" in default:
                    continue
                offenders.append(f"{py}: or 默认值 {default!r}")
    assert not offenders, "KG_MERGED_DIR 默认值不统一（应为 candidate_v3）:\n" + "\n".join(offenders)


def test_no_hardcoded_root_merged_paths_in_src():
    offenders = []
    for root in _scan_roots():
        for py in root.rglob("*.py"):
            if py.name.startswith("test_") and "tests" in str(py):
                # mra 测试锚定 candidate_v3 的绝对路径推导合法（parents[3] 写法）
                continue
            text = py.read_text(errors="ignore")
            for m in ROOT_MERGED_FILES.finditer(text):
                # candidate_v3 / candidate_v2 内的引用合法（测试固定数据）
                span = text[max(0, m.start() - 60):m.end()]
                if "candidate_" in span or "parents[" in span:
                    continue
                offenders.append(f"{py}: {m.group(0)}")
    assert not offenders, "src 内存在根层 data/merged 硬编码（应走 KG_MERGED_DIR）:\n" + "\n".join(offenders)


def test_anchored_defaults_resolve_to_existing_dirs():
    """Path(__file__) 锚定的默认值必须解析到真实存在的目录（监工 P0-2，2026-10-07）。

    背景：mra 源码（mra/src/mra/kg/）与测试（mra/tests/kg/）目录深度差一层，
    parents[N] 层级写错时字符串匹配仍 PASS，只有存在性检查能兜底。
    """
    import ast
    offenders = []
    for root in _scan_roots():
        for py in root.rglob("*.py"):
            text = py.read_text(errors="ignore")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                # 找 environ.get("KG_MERGED_DIR", <default>) 调用
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "KG_MERGED_DIR"
                        and len(node.args) > 1):
                    default = node.args[1]
                    # 含 candidate_v3 的锚定表达式：静默求值（无副作用的纯路径表达式）
                    src_seg = ast.get_source_segment(text, default) or ""
                    if "candidate_v3" not in src_seg or "__file__" not in src_seg:
                        continue
                    try:
                        val = eval(  # noqa: S307 —— 仅求值本仓库源码里的纯路径表达式
                            compile(ast.Expression(_strip_type_ctor(default)), "<default>", "eval"),
                            {"__file__": str(py), "Path": Path, "os": __import__("os"),
                             "str": str},
                            {})
                        resolved = Path(str(val))
                        if not (resolved / "merged_edges.tsv").exists():
                            offenders.append(f"{py}: 默认解析 {resolved} 不存在")
                    except Exception as e:  # noqa: BLE001 —— 求值失败按可解析性失败处理
                        offenders.append(f"{py}: 默认值无法求值（{type(e).__name__}）")
    assert not offenders, "KG_MERGED_DIR 锚定默认值存在解析错误:\n" + "\n".join(offenders)


def _strip_type_ctor(node):
    """str(Path(...)) 外壳在求值时可原样保留——此辅助仅防御性保留接口。"""
    return node


if __name__ == "__main__":
    test_all_kg_merged_dir_defaults_point_to_candidate_v3()
    test_no_hardcoded_root_merged_paths_in_src()
    test_anchored_defaults_resolve_to_existing_dirs()
    print("PASS: KG_MERGED_DIR 默认值全仓一致（candidate_v3），无根层硬编码，锚定默认均可解析且存在")
