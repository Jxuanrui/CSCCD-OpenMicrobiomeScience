"""从 NLM 官方 SPARQL 端点取 MeSH 树号快照（带查询原文与响应 sha）。

为什么不用完整 XML dump：实测 desc2026.gz(16.8MB)+supp2026.gz(47.3MB) 在本机
下载速率 ~7KB/s（约 2 小时），而本任务只需要"我方 386 ∪ 库 457 个 UID"的树号。
故改用官方 SPARQL 端点批量取，并落**查询原文 + 响应字节 + sha256 + 时刻**，
三者一并入档方可复现（等价"官方 API 快照"，非全量 dump——此限制写进 manifest）。

注意：补充概念（C 码）在 MeSH 里多数**没有 treeNumber**（实测 C536657 无），
这类节点无法做层级桥，只登记计数，不得当成"库里没有该病"。
"""
import hashlib
import json
import subprocess
import time
import urllib.parse
from pathlib import Path

ENDPOINT = "https://id.nlm.nih.gov/mesh/sparql"
PREFIX = ("PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#> "
          "PREFIX mesh: <http://id.nlm.nih.gov/mesh/> ")


def query(uids: list[str], with_tree: bool) -> tuple[str, bytes]:
    vals = " ".join(f"mesh:{u}" for u in uids)
    if with_tree:
        q = (PREFIX + "SELECT ?d ?tn WHERE { VALUES ?d { " + vals +
             " } ?d meshv:treeNumber ?tn . }")
    else:
        q = (PREFIX + "SELECT ?d WHERE { VALUES ?d { " + vals + " } }")
    # 用 curl 取字节：本机代理下 python urllib 会 TLS EOF（实测），curl 稳定。
    cmd = ["curl", "-sSL", "--max-time", "180", "--retry", "3",
           "--retry-delay", "3", "-G", ENDPOINT,
           "--data-urlencode", f"query={q}",
           "--data-urlencode", "format=JSON",
           "--data-urlencode", "inference=false"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    if not raw.startswith(b"{"):
        raise SystemExit(f"[mesh-sparql] 非 JSON 响应：{raw[:200]!r}")
    return q, raw


def main(uid_file: Path, out_dir: Path, batch: int = 150) -> int:
    uids = [x.strip() for x in uid_file.read_text().split() if x.strip()]
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"[mesh-sparql] {out_dir} 非空——换时间戳（fail-closed）")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"endpoint": ENDPOINT, "n_uids": len(uids),
                "batches": [], "note": "官方 SPARQL 快照，非全量 dump"}
    have_tn, no_tn = {}, []
    for i in range(0, len(uids), batch):
        chunk = uids[i:i + batch]
        q, raw = query(chunk, with_tree=True)
        sha = hashlib.sha256(raw).hexdigest()
        (out_dir / f"batch{i // batch:03d}.json").write_bytes(raw)
        rows = json.loads(raw)["results"]["bindings"]
        got = set()
        for b in rows:
            d = b["d"]["value"].rsplit("/", 1)[-1]
            tn = b["tn"]["value"].rsplit("/", 1)[-1]
            have_tn.setdefault(d, []).append(tn)
            got.add(d)
        # 哪些 UID 一条树号都没有（补充概念常见）
        missing = [u for u in chunk if u not in got]
        if missing:
            _, raw2 = query(missing, with_tree=False)
            rows2 = json.loads(raw2)["results"]["bindings"]
            present = {b["d"]["value"].rsplit("/", 1)[-1] for b in rows2}
            (out_dir / f"batch{i // batch:03d}_no_tree.json").write_bytes(raw2)
            no_tn += sorted(present)
        manifest["batches"].append({
            "range": [i, i + len(chunk)], "query": q, "response_sha256": sha,
            "rows": len(rows), "uids_without_tree_number": len(missing)})
        time.sleep(1)  # 官方端点礼貌间隔
    (out_dir / "tree_numbers.json").write_text(
        json.dumps(have_tn, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest["uids_with_tree_number"] = len(have_tn)
    manifest["uids_without_tree_number"] = no_tn
    (out_dir / "mesh_sparql_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[mesh-sparql] {out_dir}: {len(have_tn)}/{len(uids)} 有树号，"
          f"{len(no_tn)} 无树号（补充概念）")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
