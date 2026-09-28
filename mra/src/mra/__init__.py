"""MicrobiomeResearchAgent (mra)。

模型可插拔、知识可审计、权限可控的肠道菌群科研 Agent 平台。
架构与治理基线见 PLANNING.md 第 5 章；本包结构按第 9 章 Q1"接口先行"策略逐步填充。

项目级配置：包导入时加载 mra/.env（缺省回退仓库根 .env），已设的环境变量
优先、绝不覆盖——跨服务器迁移只需换 .env 不改代码。安全铁律在加载器内
机械执行：名称以 _KEY/_TOKEN/_SECRET/_PASSWORD 结尾的变量（即各类凭据）
拒载并告警，凭据只走真实环境变量，禁止落盘。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")

_HERE = Path(__file__).resolve()
DEFAULT_ENV_PATHS = (
    _HERE.parents[2] / ".env",   # mra/（uv 项目目录，项目级配置首选）
    _HERE.parents[3] / ".env",   # 仓库根（回退）
)


def _load_project_env(paths=None) -> dict[str, str]:
    """加载项目级 .env；返回实际生效的 {变量: 值}。密钥类变量拒载。

    必须在任何子模块读取 os.environ 之前执行——本函数在包 __init__ 顶部
    调用，而 Python 保证父包初始化先于子模块，故 rtools 等处的模块级
    os.environ.get() 天然看到 .env 值。
    """
    loaded: dict[str, str] = {}
    for path in (paths or DEFAULT_ENV_PATHS):
        if not Path(path).is_file():
            continue
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key.endswith(_SECRET_SUFFIXES):
                print(f"mra: 拒绝从 .env 加载凭据类变量 {key}（凭据只走环境变量，禁止落盘）",
                      file=sys.stderr)
                continue
            if key in os.environ:      # 真实环境变量优先
                continue
            os.environ[key] = value
            loaded[key] = value
    return loaded


_load_project_env()
