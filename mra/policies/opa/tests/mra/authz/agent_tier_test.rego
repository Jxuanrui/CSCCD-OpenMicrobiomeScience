package mra.authz_test

import rego.v1

agent_read_input := {
  "principal": {"type": "agent", "id": "agent-1"}, "action": "read_projection",
  "resource": {"type": "projection", "project_id": "project01", "classification": "synthetic"}, "context": {}
}

agent_execute_input := {
  "principal": {"type": "agent", "id": "agent-1"}, "action": "execute_task",
  "resource": {"type": "task", "id": "simulate_association"},
  "context": {"task_id": "simulate_association", "execution": {"network_mode": "deny", "credential_refs": []}}
}

test_agent_read_projection_allow if {
  d := data.mra.authz.decision with input as agent_read_input
  d.allow
  not d.approval_required
}

test_agent_execute_allow_with_constraints if {
  d := data.mra.authz.decision with input as agent_execute_input
  d.allow
  d.constraints.max_memory_mb == 512
  d.constraints.timeout_s == 300
}

test_agent_execute_network_must_deny if {
  i := object.union(agent_execute_input, {"context": object.union(agent_execute_input.context, {"execution": {"network_mode": "allow", "credential_refs": []}})})
  d := data.mra.authz.decision with input as i
  not d.allow
  not d.approval_required
}

test_agent_execute_credentials_must_deny if {
  i := object.union(agent_execute_input, {"context": object.union(agent_execute_input.context, {"execution": {"network_mode": "deny", "credential_refs": ["secret-ref"]}})})
  d := data.mra.authz.decision with input as i
  not d.allow
  not d.approval_required
}

test_agent_restricted_actions_deny if {
  every action in ["export_data", "manage_policy", "approve_request"] {
    i := {"principal": {"type": "agent", "id": "agent-1"}, "action": action, "resource": {"type": "request"}, "context": {}}
    d := data.mra.authz.decision with input as i
    not d.allow
    not d.approval_required
  }
}
