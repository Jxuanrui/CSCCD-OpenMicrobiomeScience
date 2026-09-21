"""KG 快照管理：从主图谱仓库只读复制 merged TSV，落为带 manifest 的版本化快照。

快照目录（var/kg_snapshots/<snapshot_id>/）：merged_nodes.tsv / merged_edges.tsv / manifest.json。
manifest 记录来源路径、行数、sha256；快照一旦写入不再修改，主图随时重跑刷新互不影响。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

NODES_FILE = "merged_nodes.tsv"
EDGES_FILE = "merged_edges.tsv"
MANIFEST_FILE = "manifest.json"

DEFAULT_SOURCE = Path("~/work/Project/Knowledge_Graph/data/merged")
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "var" / "kg_snapshots"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_data_lines(path: Path) -> int:
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f) - 1  # 减表头


def create_snapshot(
    source: Path = DEFAULT_SOURCE,
    root: Path = DEFAULT_ROOT,
    snapshot_id: str | None = None,
) -> Path:
    source, root = Path(source), Path(root)
    snapshot_id = snapshot_id or date.today().isoformat()
    dest = root / snapshot_id
    if dest.exists():
        raise FileExistsError(f"快照已存在：{dest}（快照不可变，请换 snapshot_id）")
    files: dict[str, dict] = {}
    for fname in (NODES_FILE, EDGES_FILE):
        src = source / fname
        if not src.is_file():
            raise FileNotFoundError(f"源目录缺少 {fname}：{source}")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / fname)
        files[fname] = {
            "sha256": _sha256(dest / fname),
            "data_lines": _count_data_lines(dest / fname),
        }
    manifest = {
        "snapshot_id": snapshot_id,
        "created": date.today().isoformat(),
        "source": str(source),
        "files": files,
    }
    (dest / MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def list_snapshots(root: Path = DEFAULT_ROOT) -> list[dict]:
    root = Path(root)
    if not root.is_dir():
        return []
    out = []
    for manifest_path in sorted(root.glob(f"*/{MANIFEST_FILE}")):
        out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    return out


def latest_snapshot(root: Path = DEFAULT_ROOT) -> Path:
    snaps = list_snapshots(root)
    if not snaps:
        raise FileNotFoundError(f"没有任何快照，请先 create_snapshot（root={root}）")
    return Path(root) / snaps[-1]["snapshot_id"]
