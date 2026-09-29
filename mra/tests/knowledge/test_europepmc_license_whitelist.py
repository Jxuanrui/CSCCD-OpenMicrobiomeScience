#!/usr/bin/env python3
"""P0-K：全文许可证白名单（用户拍板#5 保守政策）离线回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mra.knowledge.sources.europepmc import is_reusable_fulltext  # noqa: E402


def test_whitelist_accepts():
    for lic in ("CC BY 4.0", "cc by", "CC0", "CC0 1.0", "Public domain", "CC BY 3.0"):
        assert is_reusable_fulltext(lic), lic


def test_whitelist_rejects():
    for lic in ("CC BY-NC 4.0", "CC BY-NC-SA", "CC BY-ND", "CC BY-SA 3.0",
                "", "free to read", "unspecified", "NO LICENSE"):
        assert not is_reusable_fulltext(lic), lic
