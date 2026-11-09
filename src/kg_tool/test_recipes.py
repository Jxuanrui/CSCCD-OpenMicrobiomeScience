"""配方层测试（轨C 两护栏的机器化）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from kg_tool import recipes

ALLOWED = {"recommended", "not_recommended", "candidate"}


def _entries():
    d = recipes.load("htn")
    for cat, entries in d.items():
        if cat.startswith("_") or cat == "key" or "_note" in entries:
            continue
        for tool, e in entries.items():
            yield cat, tool, e


def test_schema_and_status():
    for cat, tool, e in _entries():
        assert e["status"] in ALLOWED, f"{tool}: 非法状态 {e['status']}"
        assert e.get("reason") and e.get("evidence"), f"{tool}: 缺理由/证据"


@pytest.mark.needs_data
def test_evidence_files_exist_on_disk():
    """护栏①机器化：recommended/not_recommended 的证据档案必须在盘。"""
    for cat, tool, e in _entries():
        if e["status"] in ("recommended", "not_recommended"):
            p = recipes.ROOT / e["evidence"]
            assert p.exists(), f"{tool}: 证据档案不在盘 {p}"


def test_candidate_never_recommends():
    """护栏②：candidate 词条不得出现推荐措辞。"""
    for cat, tool, e in _entries():
        if e["status"] == "candidate":
            assert "推荐" not in e["reason"].split("——")[0]


def test_unknown_recipe_fails_closed():
    with pytest.raises(SystemExit):
        recipes.load("no_such")


def test_note_and_entries_mutually_exclusive():
    """P1-1（轨C 审）：含 _note 的类目不得混入词条——否则护栏整体静默失效。"""
    d = recipes.load("htn")
    for cat, entries in d.items():
        if cat.startswith("_") or cat == "key":
            continue
        has_note = "_note" in entries
        n_tools = sum(1 for k, v in entries.items()
                      if not k.startswith("_") and isinstance(v, dict))
        if has_note:
            assert n_tools == 0, f"{cat}: _note 与词条混存——护栏会静默跳过"
        else:
            assert n_tools > 0, f"{cat}: 既无 _note 也无词条——类目悬空"
