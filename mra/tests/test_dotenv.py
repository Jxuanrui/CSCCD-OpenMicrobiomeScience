"""项目级 .env 加载行为检查：优先级、密钥拒载、解析规则。"""
from __future__ import annotations

import os

import pytest

from mra import _load_project_env


@pytest.fixture
def clean_environ(monkeypatch):
    """快照并清理测试用变量，结束自动还原。"""
    keys = ["MRA_DOTENV_TEST_A", "MRA_DOTENV_TEST_B", "MRA_DOTENV_TEST_KEY",
            "MRA_DOTENV_TEST_TOKEN"]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    yield
    for k in keys:
        os.environ.pop(k, None)


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_loads_values_and_respects_env_precedence(tmp_path, clean_environ):
    env_file = tmp_path / ".env"
    os.environ["MRA_DOTENV_TEST_A"] = "from-shell"   # 真实环境变量优先
    _write(env_file, "MRA_DOTENV_TEST_A=from-file\nMRA_DOTENV_TEST_B=from-file\n")
    loaded = _load_project_env([env_file])
    assert os.environ["MRA_DOTENV_TEST_A"] == "from-shell"
    assert os.environ["MRA_DOTENV_TEST_B"] == "from-file"
    assert loaded == {"MRA_DOTENV_TEST_B": "from-file"}


def test_refuses_secret_named_vars(tmp_path, clean_environ, capsys):
    env_file = tmp_path / ".env"
    _write(env_file, "MRA_DOTENV_TEST_KEY=sk-xxx\nMRA_DOTENV_TEST_TOKEN=ghp_xxx\n")
    loaded = _load_project_env([env_file])
    assert loaded == {}
    assert "MRA_DOTENV_TEST_KEY" not in os.environ
    assert "凭据" in capsys.readouterr().err  # 拒载有告警


def test_parsing_rules(tmp_path, clean_environ):
    env_file = tmp_path / ".env"
    _write(env_file, "# 注释\n\nMRA_DOTENV_TEST_A='带引号'\n  MRA_DOTENV_TEST_B = 无引号  \nbroken-line-no-eq\n")
    _load_project_env([env_file])
    assert os.environ["MRA_DOTENV_TEST_A"] == "带引号"
    assert os.environ["MRA_DOTENV_TEST_B"] == "无引号"


def test_missing_file_is_silent_noop(tmp_path):
    assert _load_project_env([tmp_path / "nope.env"]) == {}
