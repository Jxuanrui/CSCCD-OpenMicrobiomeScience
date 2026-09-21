from __future__ import annotations

import shutil
import subprocess

import pytest

from mra.pep.systemd_runner import systemd_scope_runner


def _systemd_scope_available() -> bool:
    if shutil.which("systemd-run") is None:
        return False
    try:
        result = systemd_scope_runner("true", [], {}, 10.0)
        return result.returncode == 0
    except OSError:
        return False


requires_systemd_scope = pytest.mark.skipif(
    not _systemd_scope_available(),
    reason="systemd-run --user --scope unavailable on this host",
)


@requires_systemd_scope
def test_scope_enforces_memory_limit() -> None:
    result = systemd_scope_runner(
        "python3",
        ["-c", "x = bytearray(2 * 1024 ** 3)"],
        {"max_memory_mb": 64},
        30.0,
    )
    assert result.returncode != 0


@requires_systemd_scope
def test_scope_allows_small_task() -> None:
    result = systemd_scope_runner(
        "python3",
        ["-c", "print(42)"],
        {"max_memory_mb": 256},
        30.0,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "42"


def test_scope_runner_uses_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("ARK_API_KEY", "do-not-leak")
    monkeypatch.setattr(subprocess, "run", fake_run)
    systemd_scope_runner("true", (), {}, 1.0)
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert "ARK_API_KEY" not in environment
    # 仅允许基础运行时变量 + systemd 用户总线连接变量；其余（含凭据）一律不继承
    assert set(environment) <= {
        "PATH",
        "LANG",
        "LC_ALL",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    }
