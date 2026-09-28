package mra.authz

import rego.v1

valid_receipt(req) if {
  receipt := req.approval_receipt
  receipt.request_id == req.context.request_id
  receipt.subject_hash == req.context.subject_hash
  receipt.approver.type == "human"
  receipt.status == "approved"
  receipt.policy_revision == data.mra.meta.revision
}
