import type { Rule, RuleDraft } from './api'

// Cross-tab handoffs into the Rule Workbench: "Create a Rule" carrying a
// run's pre-validated draft artifacts (pure navigation, no LLM call —
// ADR-0006), or "Edit" from a saved Rule (which reopens the Candidate).
export type Handoff =
  | { kind: 'draft'; rule: RuleDraft }
  | { kind: 'edit'; rule: Rule }
