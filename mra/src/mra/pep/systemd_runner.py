"""systemd-run --user --scope 执行器：为 execute_task 提供 OS 级资源限制。

依据 PLANNING.md 5.14-Q3 / 第 9 章 Q11：MVP 受控执行的资源隔离用
`systemd-run --user` scope（MemoryMax/CPUQuota 已于 2026-09-10 实测生效），
Apptainer SIF 管线迭代 2 引入。此执行器映射 OPA constraints 中的
max_memory_mb / cpu_quota_percent 到 systemd 属性；网络隔离留待 Apptainer。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence


def systemd_scope_runner(
    command: str,
    args: Sequence[str],
    constraints: Mapping[str, object],
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """Run ``command args`` inside a transient systemd user scope with limits.

    Returns a ``subprocess.CompletedProcess``（PEP 的 ``_command_result`` 可解析）。
    若 ``systemd-run`` 不可用或宿主无 user cgroup 委托，会抛 OSError，由调用方
    （PEP）按"任务执行失败"处理。
    """
    cmd = ["systemd-run", "--user", "--scope", "--collect"]
    memory_mb = constraints.get("max_memory_mb")
    cpu_pct = constraints.get("cpu_quota_percent")
    if (
        isinstance(memory_mb, (int, float))
        and not isinstance(memory_mb, bool)
        and memory_mb > 0
    ):
        cmd += ["-p", f"MemoryMax={int(memory_mb)}M"]
    if (
        isinstance(cpu_pct, (int, float))
        and not isinstance(cpu_pct, bool)
        and cpu_pct > 0
    ):
        cmd += ["-p", f"CPUQuota={int(cpu_pct)}%"]
    cmd += ["--", command, *args]
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    # systemd-run --user 需连接用户总线，须保留这两个变量；其余（含 ARK_API_KEY
    # 等凭据）一律不继承（默认拒绝）。
    for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        if key in os.environ:
            environment[key] = os.environ[key]
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
        shell=False,
        env=environment,
    )


__all__ = ["systemd_scope_runner"]
