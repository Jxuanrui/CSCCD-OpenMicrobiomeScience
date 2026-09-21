from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mra.pep import OpaClient, OpaError


def decision_json(**overrides: object) -> str:
    result: dict[str, object] = {
        "allow": True,
        "approval_required": False,
        "reason_codes": ["ALLOW_TEST"],
        "constraints": {"timeout_s": 10},
        "policy_revision": "test-revision",
    }
    result.update(overrides)
    return json.dumps(result)


def test_opa_builds_exact_input_and_uses_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, decision_json(), "")

    monkeypatch.setattr(subprocess, "run", run)
    client = OpaClient(tmp_path / "opa", tmp_path / "bundle", timeout_s=2)
    result = client.evaluate(
        principal_type="agent",
        principal_id="fixed-agent",
        action="execute_task",
        resource={"type": "task", "id": "simulate_association"},
        context={
            "task_id": "simulate_association",
            "request_id": "request-1",
            "execution": {"network_mode": "deny", "credential_refs": []},
        },
    )

    command, kwargs = calls[0]
    submitted = json.loads(str(kwargs["input"]))
    assert set(submitted) == {"principal", "action", "resource", "context"}
    assert submitted["principal"] == {"type": "agent", "id": "fixed-agent"}
    assert submitted["context"]["execution"] == {
        "network_mode": "deny",
        "credential_refs": [],
    }
    assert command[-2:] == ["--stdin-input", "data.mra.authz.decision"]
    assert kwargs["timeout"] == 2
    assert result.allow is True
    assert result.decision_id


@pytest.mark.parametrize(
    ("resource", "context"),
    [
        ({"type": "task", "unexpected": "value"}, {}),
        ({"type": "task"}, {"principal": {"type": "human"}}),
        (
            {"type": "task"},
            {"execution": {"network_mode": "deny", "secret": "value"}},
        ),
    ],
)
def test_opa_rejects_additional_input_properties(
    resource: dict[str, object], context: dict[str, object]
) -> None:
    with pytest.raises(OpaError):
        OpaClient.build_input(
            principal_type="agent",
            principal_id="fixed-agent",
            action="execute_task",
            resource=resource,
            context=context,
        )


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not-json",
        "{}",
        '{"result":[]}',
        decision_json(reason_codes=[]),
        decision_json(allow=True, approval_required=True),
        decision_json(constraints=[]),
    ],
)
def test_opa_invalid_output_fails_closed(output: str) -> None:
    with pytest.raises(OpaError):
        OpaClient._parse_output(output)


def test_opa_subprocess_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, "", "secret"),
    )
    client = OpaClient(tmp_path / "opa", tmp_path / "bundle")

    with pytest.raises(OpaError, match="failed closed") as error:
        client.evaluate(
            principal_type="agent",
            principal_id="fixed-agent",
            action="manage_policy",
            resource={"type": "policy"},
            context={},
        )

    assert "secret" not in str(error.value)
