"""欢迎页行为检查：python -m mra 静态输出关键内容、退出码恒 0、不显密钥值。"""
from __future__ import annotations

from mra.__main__ import main


def test_welcome_static_output(capsys, monkeypatch):
    monkeypatch.setenv("MRA_NO_BANNER", "1")
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "菌群科研 Agent" in out            # 项目定位
    assert "执行链条" in out                   # 逻辑介绍
    assert "python -m mra.research" in out     # 使用速查
    assert "环境自检" in out                   # 自检区块
    assert "ARK_API_KEY" in out                # 密钥只报名不报值
    import re
    assert not re.search(r"ARK_API_KEY[=:]\S", out)


def test_welcome_no_banner_flag(capsys, monkeypatch):
    monkeypatch.delenv("MRA_NO_BANNER", raising=False)
    assert main(["--no-banner"]) == 0
    assert "\033[" not in capsys.readouterr().out  # 静态模式无ANSI控制序列
