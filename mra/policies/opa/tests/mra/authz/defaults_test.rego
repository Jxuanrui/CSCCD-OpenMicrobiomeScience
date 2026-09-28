package mra.authz_test

import rego.v1

human_input(action) := {"principal": {"type": "human", "id": "human-1"}, "action": action, "resource": {"type": "request"}, "context": {}}

test_human_all_known_actions_allow if {
  every action in data.mra.actions {
    d := data.mra.authz.decision with input as human_input(action)
    d.allow
    not d.approval_required
  }
}

test_unknown_action_denied if {
  d := data.mra.authz.decision with input as human_input("unknown_action")
  not d.allow
  not d.approval_required
  d.reason_codes == ["DENY_DEFAULT"]
}

test_unknown_principal_denied if {
  i := object.union(human_input("read_projection"), {"principal": {"type": "robot", "id": "human-1"}})
  d := data.mra.authz.decision with input as i
  not d.allow
  not d.approval_required
}

test_missing_fields_denied if {
  d := data.mra.authz.decision with input as {"principal": {"type": "agent"}, "action": "read_projection"}
  not d.allow
  not d.approval_required
  d.reason_codes == ["DENY_DEFAULT"]
}

test_output_shape_stable_on_default if {
  d := data.mra.authz.decision with input as {}
  d.allow == false
  d.approval_required == false
  is_array(d.reason_codes)
  is_object(d.constraints)
  is_string(d.policy_revision)
}
