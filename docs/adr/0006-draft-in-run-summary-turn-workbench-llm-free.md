# The Run summary turn drafts the rule; the Workbench makes no LLM calls

> Amended (2026-07-19): superseded in part by ADR-0007. The `no_rule` path
> described below let the drafting LLM refuse a rule based on its own read of
> the result rows ("purely informational", "cannot stand on transaction
> attributes alone"). That is a veto over the FSM's hypothesis, which the
> assignment gives to the backtest, not to the assistant; it wrongly refused
> plain segment queries (e.g. listing transactions in Rome) whose rows carried
> no fraud label to judge. ADR-0007 replaces that judgment with a structural
> decision: the turn translates the segment the query names into a clause and
> declines only when the run names no deployable segment. Read every "no_rule"
> below as ADR-0007's structural decline, not a risk verdict on the rows.

Amends ADR-0005, which made "Create a Rule" hand the Explore chat and its query to a separate Drafter call that fills the Workbench fields. Using that flow surfaced two problems. The fields take a full LLM round-trip to fill at exactly the moment of intent, so the Workbench sits in a skeleton state for seconds (issue #10 treated the symptom). And the Drafter never sees result rows, only the chat text and the SQL, so it must refuse whenever the concrete values a clause needs were not spelled out in conversation, sending the FSM back to run another query first.

The resolution (issue #15): the post-Run summary turn, the one LLM call that already sees the question, the SQL, and the real result rows, also returns either a rule (`{clause, name, description}`) or an explicit `no_rule` reason. The clause is validated inline through `GuardedExecutor.validate_clause` (guardrails plus the fraud_labels deployability ban from issue #13) inside the same bounded repair loop the chat uses. The artifacts attach to the run card, "Create a Rule from this" becomes pure navigation carrying pre-validated fields, and the Workbench makes no LLM calls at all: it pre-fills, lets the FSM edit, backtests, and saves. `drafter.py` and `/api/rules/draft` are deleted; every LLM interaction now lives in the Explore tab. Prototyped in `backend/prototypes/summary_draft*.py` (throwaway prototypes, since removed once the design shipped in `summary_drafter.py`); driving it showed grounded IN-lists drawn from real rows and an instant handoff.

Refinements over the prototype, addressing the weaknesses driving it exposed:

- The combined turn replays the whole conversation (`serialize_turn`), not just the last user message, so constraints stated in earlier turns reach the draft. The chat turn already replays full history on every message; the run turn doing the same is symmetric cost.
- A draft is a snapshot of its run. Chat after a run never mutates it; re-running a (possibly revised) query regenerates both summary and draft, and each run card owns its own artifacts.
- Failure never hides evidence: rows always return. On repair-loop exhaustion the turn degrades to the summary plus a `no_rule` reason; on `LLMUnavailableError` the run keeps the existing fallback summary and simply carries no rule.
- Aggregate rows stay draftable: the prompt explicitly allows dimension-table subqueries when the rows are aggregate evidence about a named segment (a city-level fraud rate becomes `merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')`). Driving the prototype produced a wrong `no_rule` on exactly this shape.

## Considered Options

- **Keep the separate Drafter but feed it the run's rows at Create time**: fixes grounding, but keeps the intent-time spinner and a second prompt duplicating the grounding rules. Rejected: the latency at the moment of intent is the complaint.
- **Draft lazily with a background prefetch on run**: the best cost profile, but adds cancellation, races, and invalidation to thread state to hide a spinner. Rejected as complexity out of proportion.
- **Chosen: one combined summarize-and-draft turn per run**, artifacts carried as pre-validated data on the run card.

## Trade-offs accepted

- Every run pays drafting output tokens and possible repair attempts, though most runs never become rules. Summary latency is now bounded by the drafting repair loop (at most `max_translation_attempts` calls); the degrade path returns the summary either way.
- One prompt does three jobs (summary quality, rule quality, `no_rule` honesty), so a regression in one can leak into the others. The rule-quality evals (issue #12) retarget the combined turn, including `no_rule` honesty cases over informational queries.
- PRD story 11 (a rule from plain English without prior exploration) remains the hand-written-clause Workbench entry from ADR-0005; with the LLM out of the Workbench there is no natural-language path there. Explore is the natural-language path: ask, run, create.
- Truncated result rows can still yield incomplete enumerations; the description must say so, and the row cap bounds what a draft can enumerate.
