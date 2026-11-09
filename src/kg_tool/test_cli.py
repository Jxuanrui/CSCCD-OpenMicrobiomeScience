"""kg CLI 薄转发层测试（阶段 1 W1.2）。

测三件事：①--version 单源 ②转发 argv 原样到达模块 main 且退出码透传
③MODULES 登记的模块文件真实存在。不真跑模块——golden 等价由
scripts/golden_check.py 用真实链路验证，测试只锁转发机制。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ 入 path

from typer.testing import CliRunner  # noqa: E402

import kg_tool  # noqa: E402
import kg_tool.cli as cli  # noqa: E402

runner = CliRunner()


def test_version_single_source():
    """--version 输出必须与 kg_tool.__version__ 单源一致。"""
    r = runner.invoke(cli.app, ["--version"])
    assert r.exit_code == 0
    assert f"kg {kg_tool.__version__}" in r.output


def test_dispatch_forwards_argv_and_exit_code(monkeypatch):
    """转发机制：argv 原样到达模块 main，退出码透传（用假模块，不真跑）。"""
    seen = {}

    class FakeMod:
        @staticmethod
        def main(argv):
            seen["argv"] = list(argv)
            return 7  # 故意非零，验证退出码透传

    monkeypatch.setattr(cli, "_load", lambda mf: FakeMod())
    r = runner.invoke(cli.app,
                      ["subgraph", "tier0", "--mesh", "D006973", "--out", "/tmp/x"])
    assert seen["argv"] == ["tier0", "--mesh", "D006973", "--out", "/tmp/x"]
    assert r.exit_code == 7


def test_registered_module_files_exist():
    """MODULES 登记表与磁盘一致性：八件模块文件必须全部在 06_tool 下。"""
    for name, (mf, _doc) in cli.MODULES.items():
        assert (cli.ROOT / "src" / mf).exists(), f"kg {name} -> {mf} 不存在"
