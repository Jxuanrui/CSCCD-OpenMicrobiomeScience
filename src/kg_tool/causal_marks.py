"""U1 因果语言标注 sidecar（B1/U1，spec v1.1 词表逐字冻结）。

定位与红线：
- 机械词表扫描（大小写不敏感 + \\b 词边界）——零 LLM、零重抽、零排序变更；
- 只读 relation_assertions.tsv，只增 sidecar causal_language_marks.tsv
  到同一子图目录（只增不改区：sidecar 已存在且字节一致=幂等 no-op；
  字节不一致=fail-closed 拒写——那意味着断言原文或词表漂移，必须
  人工移走旧档并过审后再重跑，防两次运行产物混放）；
- 与子图构建期 CAUSAL_REGEX 的关系=细化扩展非替代（spec §机制复用声明）：
  两系统口径差异源于粒度不同，CAUSAL_REGEX 原口径保留不动。

T0 实现细节裁定（spec 未细指，记 CHANGELOG 台账）：
- hit_words=按冻结词表**原词**记录命中词（不记句中大小写变体），
  causal 类在前 correlational 类在后、各类内按词表给定顺序，去重逗号拼接
  ——保证该列可直接与冻结词表对账（改词表=过审的可审计性来源）；
- n_causal_words=hit_words 中 causal 类词数（与 hit_words 自洽可核）；
- sentence_sha16=sha256(sentence_utf8).hexdigest()[:16]（仓内 sha16 通行
  约定，同 inference.py KO_MAP 校验口径），作原文指纹防漂移。
"""
import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------
# 词表逐字冻结（spec v1.1，2026-11-05）——改这里=过审（监工+用户），
# 任何"顺手补一个词"都是违例：词表是预注册口径，不是待办清单。
# ---------------------------------------------------------------
CAUSAL_TRIGGERS = [
    "induces", "promotes", "inhibits", "suppresses", "increases",
    "decreases", "reduces", "enhances", "activates", "blocks", "causes",
    "leads to", "results in", "drives", "regulates", "stimulates",
    "produces", "converts", "degrades", "secret", "secrete", "secretes",
    "secreted", "secretion", "converts to", "degraded",
]  # v1.1 补齐屈折形（监工 P2-1）
CORRELATIONAL_TRIGGERS = [
    "associated with", "correlated with", "linked to", "related to",
    "enriched in", "depleted in", "abundance of", "positively associated",
    "negatively associated", "co-occurred",
]

# 输出 schema 5 列冻结（spec v1.1）
SIDECAR_COLUMNS = ["assertion_id", "mark", "hit_words", "sentence_sha16",
                   "n_causal_words"]
SIDECAR_NAME = "causal_language_marks.tsv"
MARKS = ("causal", "correlational", "none")


def _compile(triggers: list[str]) -> list[tuple[str, re.Pattern]]:
    """词表→(原词, 编译正则) 对。

    为什么逐词 re.escape：词表含空格短语（"leads to"）与普通词，escape 后
    统一加 \\b 边界——机械、无歧义、不受词表内容影响；IGNORECASE 落实
    spec"大小写不敏感"。
    """
    return [(w, re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE))
            for w in triggers]


CAUSAL_PATTERNS = _compile(CAUSAL_TRIGGERS)
CORRELATIONAL_PATTERNS = _compile(CORRELATIONAL_TRIGGERS)


def sentence_sha16(sentence: str) -> str:
    """原文指纹：sha256 前 16 hex——sidecar 与断言原文的锚定。"""
    return hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:16]


def scan_sentence(sentence: str) -> tuple[str, str, int]:
    """单句机械扫描 → (mark, hit_words, n_causal_words)。

    命中规则（spec 冻结）：多类命中按 causal 优先**记类**，但 hit_words
    留存**全部**命中触发词（两类都记）；零命中 mark="none"。
    """
    causal_hits = [w for w, pat in CAUSAL_PATTERNS if pat.search(sentence)]
    corr_hits = [w for w, pat in CORRELATIONAL_PATTERNS
                 if pat.search(sentence)]
    if causal_hits:
        mark = "causal"
    elif corr_hits:
        mark = "correlational"
    else:
        mark = "none"
    hit_words = ",".join(causal_hits + corr_hits)
    return mark, hit_words, len(causal_hits)


def annotate(sg_dir: Path) -> dict:
    """扫描子图断言表并落 sidecar。

    只增不改保障：sidecar 已存在时与本次确定性产物逐字节比对——
    一致=幂等 no-op（exit 信息提示），不一致=SystemExit 拒写（fail-closed）。
    返回统计 dict（行数/mark 分布/幂等信息），供 CLI 汇报与测试断言。
    """
    src = sg_dir / "relation_assertions.tsv"
    if not src.exists():
        raise SystemExit(f"[causal-marks] 缺输入 {src}——sidecar 无从扫描")
    out_path = sg_dir / SIDECAR_NAME

    with open(src, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # 必需列 fail-closed：缺列的断言表无法零跳过地对齐
        missing = [c for c in ("assertion_id", "sentence")
                   if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"[causal-marks] relation_assertions.tsv 缺列 "
                             f"{missing}——fail-closed")
        lines = ["\t".join(SIDECAR_COLUMNS)]
        counts = {m: 0 for m in MARKS}
        n_rows = 0
        for row in reader:
            aid = (row.get("assertion_id") or "").strip()
            if not aid:
                raise SystemExit(
                    f"[causal-marks] 第 {n_rows + 2} 行 assertion_id 为空"
                    "——fail-closed（sidecar 每行必须可回连断言）")
            sentence = row.get("sentence") or ""
            mark, hit_words, n_causal = scan_sentence(sentence)
            counts[mark] += 1
            n_rows += 1
            # 行序=断言原序：sidecar 不引入任何排序（零排序变更红线）
            lines.append("\t".join((aid, mark, hit_words,
                                    sentence_sha16(sentence),
                                    str(n_causal))))
    payload = "\n".join(lines) + "\n"

    if out_path.exists():
        old = out_path.read_bytes()
        new = payload.encode("utf-8")
        if old == new:
            return {"n_rows": n_rows, "counts": counts,
                    "idempotent": True, "out": str(out_path)}
        raise SystemExit(
            f"[causal-marks] {out_path} 已存在且与本次产物不一致——"
            "只增不改区拒写（疑原文/词表漂移；人工移走旧档过审后重跑）")
    out_path.write_text(payload, encoding="utf-8")
    return {"n_rows": n_rows, "counts": counts, "idempotent": False,
            "out": str(out_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="U1 因果语言标注 sidecar（机械词表扫描，零 LLM）")
    ap.add_argument("--subgraph-dir", required=True, type=Path,
                    help="子图目录（读 relation_assertions.tsv，"
                         "增写 causal_language_marks.tsv）")
    args = ap.parse_args(argv)

    stats = annotate(args.subgraph_dir)
    c = stats["counts"]
    verb = "幂等 no-op（已存在且字节一致）" if stats["idempotent"] else "新写入"
    print(f"[causal-marks] {stats['out']} {verb}")
    print(f"[causal-marks] 断言 {stats['n_rows']} 行零跳过 | "
          f"mark 分布：causal={c['causal']} "
          f"correlational={c['correlational']} none={c['none']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
