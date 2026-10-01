"""safe_clean 最小回归（监工计划 v2 P0-1：Q3 事故用例——含'暂未落盘'字样的兄弟对象必须存活）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from safe_clean import safe_clean


def _tmp_json(tmp_path, payload):
    p = tmp_path / "x.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_dry_run_default_no_write(tmp_path):
    p = _tmp_json(tmp_path, {"a": {"bad": 1, "keep": 2}})
    r = safe_clean(p, ["a.bad"])
    assert r["mode"] == "dry-run" and len(r["would_delete"]) == 1
    assert "bad" in json.loads(p.read_text())["a"]  # 未写盘


def test_placeholder_sibling_survives(tmp_path):
    # Q3 事故最小复现：目标键删除时，同层含"暂未落盘"字样的兄弟对象必须存活
    p = _tmp_json(tmp_path, {"ruling": "暂未落盘为正式文件", "stale": {"x": 1}, "live": 3})
    r = safe_clean(p, ["stale.x"], apply=True)
    d = json.loads(p.read_text())
    assert r["mode"] == "applied" and d["stale"] == {}
    assert d["ruling"] == "暂未落盘为正式文件" and d["live"] == 3  # 兄弟对象存活


def test_wildcard_rejected_and_missing_reported(tmp_path):
    p = _tmp_json(tmp_path, {"a": 1})
    with pytest.raises(ValueError):
        safe_clean(p, ["a.*"])
    assert safe_clean(p, ["no.such.key"])["missing"] == ["no.such.key"]


def test_apply_makes_backup(tmp_path):
    p = _tmp_json(tmp_path, {"a": {"b": 1}})
    r = safe_clean(p, ["a.b"], apply=True)
    bdir = Path(r["backup"]); assert bdir.is_dir() and any(f.name.endswith(".tar") for f in bdir.iterdir())


def test_backup_goes_to_data_backups(tmp_path, monkeypatch):
    # C0-3：备份必须落 data/backups/<ts>/（目录树 <root>/data/merged/x.json → <root>/data/backups/）
    import shutil as _sh
    root = tmp_path / "proj"
    (root / "data/merged").mkdir(parents=True)
    p = root / "data/merged/x.json"
    p.write_text('{"a": {"b": 1}}', encoding="utf-8")
    r = safe_clean(p, ["a.b"], apply=True)
    assert str(root / "data/backups") in r["backup"]


def test_atomic_write_no_tmp_left(tmp_path):
    p = _tmp_json(tmp_path, {"a": {"b": 1}})
    safe_clean(p, ["a.b"], apply=True)
    assert not list(tmp_path.glob("*.tmp"))  # 原子写不留临时文件
