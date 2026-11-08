"""MCP 插座测试（轨D 五护栏机器化 + 内存客户端转发正确性）。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client

from kg_tool import mcp_server


def test_stdio_only_and_no_network_tokens():
    """护栏②③：transport 白名单只有 stdio；源码禁网络监听词族。"""
    src = Path(mcp_server.__file__).read_text(encoding="utf-8")
    end = src.index('"""', src.index('"""') + 3) + 3  # 剥说明书（护栏描述含禁词不算泄漏）
    src = src[end:]
    banned = ("socket", "listen(", "bind(", "0.0.0.0", "127.0.0.1",
              "localhost", "streamable", "sse", "http")
    for line in src.splitlines():
        if line.strip().startswith("#"):
            continue  # 护栏说明注释可含禁词（无调用语义）
        for b in banned:
            assert b not in line, f"网络监听词族泄漏 {b}: {line}"
    assert 'choices=["stdio"]' in src


def test_tool_module_not_in_06_tool():
    """护栏③：06_tool 主模块 mcp/llm 字样零命中。"""
    tool_dir = Path(__file__).resolve().parents[1] / "06_tool"
    for f in tool_dir.glob("*.py"):
        if f.name.startswith("test_"):
            continue
        src = f.read_text(encoding="utf-8").lower()
        # 剥文档字符串粗判：只查 import/调用行含 mcp/llm
        for line in src.splitlines():
            t = line.strip()
            if t.startswith("#"):
                continue
            if ("mcp" in t or "llm" in t) and ("import" in t):
                raise AssertionError(f"{f.name}: mcp/llm 泄漏 {line}")


def _run(coro):
    return asyncio.run(coro)


def test_inmemory_client_list_and_call():
    """验收清单①：官方 Client 内存连接（零子进程零网络）。"""
    async def scenario():
        async with Client(mcp_server.mcp) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            assert {"list_diseases", "list_cohorts", "get_top_hypotheses",
                    "get_analysis_recipes", "run_pipeline"} <= names
            res = await client.call_tool("list_diseases", {})
            import json
            got = json.loads(res.content[0].text)
            assert "htn" in got and isinstance(got, list)
            res2 = await client.call_tool("get_analysis_recipes",
                                          {"disease": "htn"})
            got2 = json.loads(res2.content[0].text)
            assert got2["differential_abundance"]["MaAsLin2"]["status"] \
                == "recommended"
    _run(scenario())


def test_run_pipeline_argv_fingerprint(tmp_path, monkeypatch):
    """P0-2（轨D 审/survey §五-6）：run_pipeline 组装的 argv 与直调
    'kg run --disease d --cohort c --tier2-batch b --out-dir o' 逐字相同。"""
    import json as _json
    sys.path.insert(0, str(mcp_server.ROOT / "src" / "06_tool"))
    import runner
    seen = {}

    def fake_main(argv):
        seen["argv"] = list(argv)
        (Path(argv[argv.index("--out-dir") + 1]) / "run_state.json"
         ).parent.mkdir(parents=True, exist_ok=True)
        (Path(argv[argv.index("--out-dir") + 1]) / "run_state.json"
         ).write_text(_json.dumps({"steps": {}}), encoding="utf-8")
        return 0

    monkeypatch.setattr(runner, "main", fake_main)
    out = mcp_server.run_pipeline("htn", "htn", "/tmp/batch", str(tmp_path / "o"))
    assert seen["argv"] == ["--disease", "htn", "--cohort", "htn",
                            "--tier2-batch", "/tmp/batch",
                            "--out-dir", str(tmp_path / "o")]
    assert _json.loads(out)["runner_rc"] == 0
