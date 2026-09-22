"""ARK LLM planner：把研究状态摘要映射为下一步受控动作（严格 JSON 输出）。

走 MRA ModelRuntime（ARK 适配器），凭据仅从环境变量 ARK_API_KEY 读取；
模型可用 ARK_PLANNER_MODEL 覆盖（默认 doubao-seed-2.0-lite，Q2 决策）。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Callable

from ..model_runtime import Message, ModelRef, ModelRequest
from ..model_runtime.ark import ArkRuntime

CAPABILITIES_PATH = Path(__file__).resolve().parents[3] / "policies" / "model_capabilities.yaml"
PRICING_PATH = Path(__file__).resolve().parents[3] / "policies" / "model_pricing.yaml"
DEFAULT_MODEL = os.environ.get("ARK_PLANNER_MODEL", "doubao-seed-2.0-lite")

SYSTEM_PROMPT = """\
你是菌群科研 Agent 的规划器（planner）。可用数据契约（暴露表/特征表/协变量）
以会话摘要中给出的为准，代码侧不作任何领域假设。

可用受控动作（每轮只选一个）：
1. {"tool":"r_association","args":{"exposure":"<暴露列名>","features":"<特征表名>","max_features":100,"q_threshold":0.05,"exposure_table":"<暴露表名，可选>"}}
   —— R 沙箱偏 Spearman（控会话协变量，BH 校正）。
2. {"tool":"kg_neighbors","args":{"term":"<菌或代谢物名>","hops":1,"categories":["Disease","Metabolite"]}}
   —— 图谱机制先验，结果带 evidence_tier(A/B)与 pmids。
3. {"tool":"kg_edge_evidence","args":{"subject":"...","object":"..."}} —— 两实体间证据。
4. {"tool":"record_finding","args":{"claim":"<一句话发现>","evidence":{...}}} —— 登记重要结论。
5. {"tool":"submit_report","args":{"summary":"<研究总结：显著关联清单、图谱支持 vs 新型模式二分、机制链示例、局限>"}}
6. {"tool":"lit_search_read","args":{"query":"<PubMed检索式>","question":"<要回答的问题>","max_results":20}}
   —— 文献检索+批量速读（消耗 LLM 预算，单次会话使用不超过 3 次；有缓存，重复同参数零消耗）。

硬性规则：
- 只输出一个 JSON 对象 {"tool":...,"args":{...},"rationale":"一句话理由"}，不得输出其他文本。
- 证据纪律：图谱结论必须引 evidence_tier；数据结论必须引 q 值与样本量；禁止无证据推断。
- r_association 结果有缓存，不要重复相同参数的分析。
- 假设检验方法论：对"暴露→特征"的关联，先检验可能的介导变量（把候选介导变量
  加入协变量重跑，观察关联衰减），再下直接/间接结论；中介臂为零则明确报告推翻。
- 停止条件：已覆盖≥3个暴露×特征组合、并对主要显著命中做过图谱交叉后，必须 submit_report。
- 预算状态可见（llm_calls/cap），规划要高效。"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("not a JSON object")
    return parsed


def make_ark_planner(
    model_name: str | None = None,
    runtime: ArkRuntime | None = None,
) -> Callable[[str], dict]:
    runtime = runtime or ArkRuntime(
        capabilities_path=CAPABILITIES_PATH, pricing_path=PRICING_PATH)
    model_ref = ModelRef(provider="ark", model=model_name or DEFAULT_MODEL,
                         version="unverified", endpoint="ark-coding")

    def planner(state_digest: str) -> dict:
        from ..budget import record_and_check
        from ..model_runtime.glm import fallback_complete, glm_available

        messages: list[Message] = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=state_digest),
        ]
        for _attempt in range(2):
            record_and_check()  # 全局日预算闸（会话级 cap 之外的第二层护栏）
            request = ModelRequest(
                request_id=uuid.uuid4().hex, model=model_ref, messages=tuple(messages))
            try:
                response = runtime.complete(request)
            except Exception:
                if not glm_available():
                    raise
                record_and_check()
                response = fallback_complete(request)
            try:
                return _extract_json(response.content)
            except (ValueError, json.JSONDecodeError):
                messages.append(Message(role="assistant", content=response.content))
                messages.append(Message(
                    role="user", content="上一条不是合法的单个 JSON 动作对象。重新输出，只输出 JSON。"))
        raise RuntimeError("planner 连续两次未输出合法 JSON")

    return planner
