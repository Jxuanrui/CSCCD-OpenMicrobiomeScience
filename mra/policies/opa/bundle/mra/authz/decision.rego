package mra.authz

import rego.v1

default decision := {
  "allow": false,
  "approval_required": false,
  "reason_codes": ["DENY_DEFAULT"],
  "constraints": {},
  "policy_revision": "draft-1"
}

decision := {
  "allow": true,
  "approval_required": false,
  "reason_codes": ["ALLOW_HUMAN"],
  "constraints": {},
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "human"
  input_shape_valid
}

decision := {
  "allow": true,
  "approval_required": false,
  "reason_codes": ["ALLOW_AGENT_READ_PROJECTION"],
  "constraints": {},
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "agent"
  input_shape_valid
  input.action == "read_projection"
  input.resource.type == "projection"
  projection_in_scope(input.resource)
}

decision := {
  "allow": true,
  "approval_required": false,
  "reason_codes": ["ALLOW_AGENT_EXECUTE_TASK"],
  "constraints": task_constraints(input.context.task_id),
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "agent"
  input_shape_valid
  input.action == "execute_task"
  task_constraints(input.context.task_id)
  execution_is_constrained(input.context)
}

decision := {
  "allow": true,
  "approval_required": false,
  "reason_codes": ["ALLOW_AGENT_PROMOTE_APPROVED"],
  "constraints": {},
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "agent"
  input_shape_valid
  input.action == "promote_result"
  valid_receipt(input)
}

decision := {
  "allow": false,
  "approval_required": true,
  "reason_codes": ["APPROVAL_REQUIRED_PROMOTE_RESULT"],
  "constraints": {},
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "agent"
  input_shape_valid
  input.action == "promote_result"
  not valid_receipt(input)
}

decision := {
  "allow": false,
  "approval_required": false,
  "reason_codes": ["DENY_AGENT_RESTRICTED_ACTION"],
  "constraints": {},
  "policy_revision": data.mra.meta.revision
} if {
  input.principal.type == "agent"
  input_shape_valid
  input.action in {"export_data", "manage_policy", "approve_request"}
}
