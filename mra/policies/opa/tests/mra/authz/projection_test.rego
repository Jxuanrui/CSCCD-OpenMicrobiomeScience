package mra.authz_test

import rego.v1

base := {"principal": {"type": "agent", "id": "agent-1"}, "action": "read_projection", "resource": {"type": "projection", "project_id": "project01", "classification": "synthetic"}, "context": {}}

test_projection_project_boundary_deny if {
  i := object.union(base, {"resource": object.union(base.resource, {"project_id": "project99"})})
  d := data.mra.authz.decision with input as i
  not d.allow
}

test_projection_classification_boundary_deny if {
  i := object.union(base, {"resource": object.union(base.resource, {"classification": "restricted"})})
  d := data.mra.authz.decision with input as i
  not d.allow
}
