package mra.authz

import rego.v1

known_action(action) if {
  action in data.mra.actions
}

known_principal(principal) if {
  principal.type in data.mra.principal_types
  is_string(principal.id)
  principal.id != ""
}

input_shape_valid if {
  is_object(input)
  is_object(input.principal)
  is_object(input.resource)
  is_object(input.context)
  known_principal(input.principal)
  known_action(input.action)
  input.resource.type in data.mra.resource_types
}

projection_in_scope(resource) if {
  some scope in data.mra.granted_scope
  scope.project_id == resource.project_id
  resource.classification in scope.classifications
  resource.type in scope.resource_types
}

task_constraints(task_id) := constraints if {
  constraints := data.mra.registered_tasks[task_id]
}

execution_is_constrained(context) if {
  context.execution.network_mode == "deny"
  count(context.execution.credential_refs) == 0
}
