"""本地单机 Web 工作台启动入口（仅回环地址，单用户）。

用法（从项目根目录）：
    uv run python -m mra.web.server
即 uvicorn 监听 127.0.0.1:8001（8000 可能被其他本地服务占用）。Pep 使用默认 var/ 路径。
"""

from __future__ import annotations

import argparse

import uvicorn

from mra.knowledge.store import KnowledgeStore
from mra.pep.pep import Pep
from mra.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="MRA 本地 Web 工作台")
    parser.add_argument("--port", type=int, default=8001, help="监听端口（默认 8001）")
    parser.add_argument("--db", default="var/knowledge/knowledge.db", help="知识库路径")
    args = parser.parse_args()
    app = create_app(Pep(), knowledge=KnowledgeStore(args.db))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()