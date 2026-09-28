"""可执行节点底座行为检查：注册表读写往返。"""
from __future__ import annotations

from mra.exec_tools import Registry, ToolCard


def test_registry_roundtrip(tmp_path):
    reg = Registry(root=tmp_path)
    card = ToolCard(name="t", repo="o/r", pmcid="PMC1", entrypoint=["echo", "hi"])
    reg.add(card)
    assert "t" in reg.list()
    loaded = reg.get("t")
    assert loaded.repo == "o/r" and loaded.entrypoint == ["echo", "hi"]
