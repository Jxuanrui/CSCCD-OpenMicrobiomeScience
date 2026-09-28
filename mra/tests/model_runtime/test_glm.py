"""GLM fallback 行为检查：ARK 失败转投、GLM 未配置时原样上抛。"""
from __future__ import annotations

import pytest

from mra.model_runtime.base import ModelRuntimeError
from mra.model_runtime.types import ModelRef, ModelResponse
from mra.research.planner import make_ark_planner


def _response(text: str) -> ModelResponse:
    from datetime import datetime, timezone

    from mra.model_runtime.types import AttemptRecord, Usage
    now = datetime.now(timezone.utc)
    ref = ModelRef(provider="ark", model="x", version="u", endpoint="ark-coding")
    return ModelResponse(request_id="r", content=text, tool_calls=(), structured=None,
                         usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
                         cost_cny=None,
                         attempts=(AttemptRecord(model=ref, started_at=now, ended_at=now,
                                                 outcome="ok"),), final_model=ref)


class _Boom:
    def complete(self, req):
        raise ModelRuntimeError("ark down")


def test_planner_falls_back_to_glm(monkeypatch):
    import mra.budget
    import mra.model_runtime.glm as glm

    monkeypatch.setattr(mra.budget, "record_and_check", lambda *a, **k: {})
    monkeypatch.setattr(glm, "glm_available", lambda: True)
    monkeypatch.setattr(glm, "fallback_complete",
                        lambda req: _response('{"tool":"kg_resolve","args":{"term":"x"}}'))
    planner = make_ark_planner(runtime=_Boom())
    assert planner("digest") == {"tool": "kg_resolve", "args": {"term": "x"}}


def test_planner_raises_when_no_fallback(monkeypatch):
    import mra.budget
    import mra.model_runtime.glm as glm

    monkeypatch.setattr(mra.budget, "record_and_check", lambda *a, **k: {})
    monkeypatch.setattr(glm, "glm_available", lambda: False)
    planner = make_ark_planner(runtime=_Boom())
    with pytest.raises(ModelRuntimeError):
        planner("digest")
