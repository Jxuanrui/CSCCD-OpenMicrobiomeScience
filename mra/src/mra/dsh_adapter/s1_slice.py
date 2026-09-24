"""S1 ACP/MCP vertical slice 的可复现驱动脚本（adapter 层，dsh 耦合唯一合法区）。

复现步骤（2026-09-24 实测通过的组合）：
  0. 前置：upstream 钉版克隆于 $DSH_SRC（默认 /tmp/dsh @46a7f68）；node>=24 与
     npx/pnpm 可用；DSH_HOME 指向隔离目录。
  1. 初始化 profile：npx -y @deepseek-ai/dsh@0.1.7-rc.1 s1head --from-default-profile headless
  2. 钉版安装回放插件：npx ... plugin --profile s1head add @deepseek-ai/dsh-llm-replay@0.1.7-rc.1
  3. 由模板克隆 fixture（模板=snapshots/session/bash-tool-turn/session.v3.jsonl）：
     只改 session id/cwd、tool 名→mcp__mra__method_query、参数、callId；
     需 3 个 assistant 结算（step3 为克隆 DONE）+ 每个 step 闭合 step/end；
     tool-call-chunks 的 dt 长度= args 数-1（校验器硬约束）；
     llm-replay 用 catch-all 模式（不带 providers，避免与真适配器撞名）。
  4. cordis 补丁：llm-replay(file=fixture) + mcp-client(stdio→mra venv python -m mra.mcp_server)。
  5. 运行：npx -y @deepseek-ai/dsh@0.1.7-rc.1 --profile s1head --patch slice.cordis.yml "<prompt>"
     （DEEPSEEK_API_KEY=dummy 仅供占位，回放不触网）。
  6. 解析 $DSH_HOME/sessions/<slug>/<sid>/session.v4.jsonl.zstd（zstdcat）→
     tool/call+tool/result；ToolExecution.governance_event_id 锚 "<session-id>#seq<N>"；
     四 ID（research_task/workspace_seq/tool_execution/session_event）互锚写入
     Workspace('s1-acp-slice')。

本文件是运行手册级脚本：fixture/补丁按上述规则重建（见 build_fixture()/
build_patch()），main() 串联全流程。保持 Scientific Core 零 dsh import。
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import time
from pathlib import Path

DSH_NPX = ["npx", "-y", "@deepseek-ai/dsh@0.1.7-rc.1"]  # 精确钉版（UPSTREAM.lock.json）
TOOL = "mcp__mra__method_query"
ARGS = json.dumps({"query": "零方差", "k": 3}, ensure_ascii=False)
CALLID = "call_00_s1mramethodquery0000000000001"


def build_fixture(template: Path, out: Path, cwd: str) -> None:
    lines = [json.loads(l) for l in template.read_text().splitlines() if l.strip()]
    now = int(time.time() * 1000)
    for e in lines:
        t = e.get("type")
        if t == "session":
            e["id"] = "s1-slice-fixture-001"; e["createdAt"] = now; e["cwd"] = cwd
        d = e.get("data") or {}
        if t == "assistant/message" and d.get("step") == 1:
            for b in d["message"]["content"]:
                if b.get("type") == "tool-call":
                    b.update(name=TOOL, id=CALLID, arguments=ARGS)
            for c in d.get("stream", []):
                chunk = c.get("chunk", c)
                if chunk.get("type") == "tool-call-chunks":
                    chunk.update(id=CALLID, name=TOOL)
                    chunk["args"] = [ARGS[i:i + 4] for i in range(0, len(ARGS), 4)]
                    chunk["dt"] = [0] * (len(chunk["args"]) - 1)
                if chunk.get("type") == "block-end" and chunk.get("block", {}).get("type") == "tool-call":
                    chunk["block"].update(name=TOOL, id=CALLID, arguments=ARGS)
        if t == "tool/call":
            d.update(name=TOOL, callId=CALLID, arguments=ARGS)
        if t == "tool/result":
            d["message"]["source"]["callId"] = CALLID
            for b in d["message"].get("content", []):
                if b.get("type") == "tool-result":
                    b["toolCallId"] = CALLID
    src = [e for e in lines if e.get("type") == "assistant/message" and e["data"].get("step") == 2][0]
    third = copy.deepcopy(src); third["data"]["step"] = 3
    out_lines, step_ends = [], 0
    for e in lines:
        if e.get("type") == "step/end":
            step_ends += 1; out_lines.append(e)
            if step_ends == 2:
                se = copy.deepcopy(e); se["data"]["step"] = 3
                ss = copy.deepcopy([x for x in lines if x.get("type") == "step/start"][0]); ss["data"]["step"] = 3
                out_lines += [ss, third, se]
            continue
        if e.get("type") == "turn/end":
            se = copy.deepcopy([x for x in lines if x.get("type") == "step/end"][0]); se["data"]["step"] = 3
            out_lines.append(se)
        out_lines.append(e)
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in out_lines))


def build_patch(out: Path, mra_dir: Path, fixture: Path) -> None:
    out.write_text(f"""- insert:
    - id: s1-llm-replay
      name: '@deepseek-ai/dsh-llm-replay'
      config:
        file: {fixture}
    - id: s1-mra-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: mra
        transport: stdio
        command: {mra_dir}/.venv/bin/python
        args: ['-m', 'mra.mcp_server']
        cwd: {mra_dir}
""")


def anchor(session_file: Path, mra_root: Path) -> dict:
    raw = subprocess.run(["zstdcat", str(session_file)], capture_output=True, text=True, check=True).stdout
    events = [json.loads(l) for l in raw.splitlines() if l.strip()]
    sid = next(e for e in events if e.get("type") == "session")
    call = next(e for e in events if e.get("type") == "tool/call")
    result = next(e for e in events if e.get("type") == "tool/result")
    turn_end = next(e for e in events if e.get("type") == "turn/end")
    payload = json.loads(result["data"]["message"]["content"][0]["text"])
    anchor_id = f"{sid['id']}#seq{call['seq']}"
    from mra.workspace import KnowledgeProvenance, ResearchTask, ToolExecution, Workspace
    import datetime
    ws = Workspace("s1-acp-slice", root=mra_root / "var" / "workspace")
    ws.append(ResearchTask(task_id="S1-SLICE-001",
                           question="dsh→MCP→ScientificCore→双账本 全链路切片",
                           client="dsh-headless", model="llm-replay(fixture)", status="done"))
    ws.append(KnowledgeProvenance(
        source_type=payload["source_type"], source_name="method_kb(via mcp)",
        source_id=payload["rules"][0]["rule_id"],
        retrieved_at=datetime.datetime.fromtimestamp(result["time"] / 1000).isoformat(),
        query="零方差", evidence_status="validated_rule"))
    ws.append(ToolExecution(
        execution_id="S1-EX-001", task_id="S1-SLICE-001", tool_name=call["data"]["name"],
        tool_type="mcp", governance_event_id=anchor_id,
        governance_verdicts=[f"session={sid['id']}",
                             f"turn_end={turn_end['data']['reason'].get('kind')}"], status="ok"))
    return {"session_id": sid["id"], "session_event_id": anchor_id,
            "rule": payload["rules"][0]["rule_id"], "turn": turn_end["data"]["reason"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsh-src", default="/tmp/dsh", help="钉版克隆目录")
    ap.add_argument("--s1-dir", default="/tmp/s1", help="切片工作目录")
    ap.add_argument("--mra-dir", required=True)
    a = ap.parse_args(argv)
    s1 = Path(a.s1_dir); s1.mkdir(parents=True, exist_ok=True)
    fixture = s1 / "fixture.session.v3.jsonl"
    build_fixture(Path(a.dsh_src) / "snapshots/session/bash-tool-turn/session.v3.jsonl", fixture, str(s1))
    build_patch(s1 / "slice.cordis.yml", Path(a.mra_dir), fixture)
    print(f"fixture+patch 就绪: {fixture}")
    print("后续运行命令见模块 docstring 第 5 步；运行后 anchor() 完成双账本互锚")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
