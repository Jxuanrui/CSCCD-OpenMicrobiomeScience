package mra.authz_test

import rego.v1

promote_base := {
  "principal": {"type": "agent", "id": "agent-1"}, "action": "promote_result",
  "resource": {"type": "result", "id": "r-1"},
  "context": {"request_id": "req-1", "subject_hash": "sha256:abc"}
}

valid_receipt := {"request_id": "req-1", "subject_hash": "sha256:abc", "approver": {"type": "human", "label": "release"}, "status": "approved", "policy_revision": "draft-1"}

test_promote_without_receipt_requires_approval if {
  d := data.mra.authz.decision with input as promote_base
  not d.allow
  d.approval_required
}

test_promote_valid_receipt_allows if {
  i := object.union(promote_base, {"approval_receipt": valid_receipt})
  d := data.mra.authz.decision with input as i
  d.allow
  not d.approval_required
}

test_promote_tampered_request_id_requires_approval if {
  r := object.union(valid_receipt, {"request_id": "req-other"})
  i := object.union(promote_base, {"approval_receipt": r})
  d := data.mra.authz.decision with input as i
  not d.allow
  d.approval_required
}

test_promote_tampered_digest_requires_approval if {
  r := object.union(valid_receipt, {"subject_hash": "sha256:tampered"})
  i := object.union(promote_base, {"approval_receipt": r})
  d := data.mra.authz.decision with input as i
  not d.allow
  d.approval_required
}

test_promote_tampered_revision_requires_approval if {
  r := object.union(valid_receipt, {"policy_revision": "old-revision"})
  i := object.union(promote_base, {"approval_receipt": r})
  d := data.mra.authz.decision with input as i
  not d.allow
  d.approval_required
}
