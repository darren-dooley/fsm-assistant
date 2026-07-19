# Rule drafting translates the query's segment; it never judges risk

Amends ADR-0006, whose combined Run turn returns `no_rule` "when the results are purely informational ... or when the pattern cannot stand on transaction attributes alone" and is told to "never force a rule out of results that do not support one." Driving the shipped product surfaced the flaw. On a plain segment query — list the transactions in Rome — the turn read result rows that carried no fraud label, saw nothing separating fraud from legitimate activity, and returned `no_rule`, refusing to draft `merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')`.

That refusal is a veto over the FSM's hypothesis. The assignment's workflow puts hypothesis-forming with the FSM and validation with historical data (the Backtest): "validate these hypotheses against historical data to find anomalous patterns." A `no_rule` that judges the rows inserts the assistant between those two steps and lets it reject a hypothesis before it is ever tested. It is also structurally unsound: exploration runs on pre-cutoff data and a listing query has no fraud column, so the model is asked to judge risk from evidence that is not present, and it will refuse exactly the segment queries the FSM most wants to test.

The resolution (issue #17): the Run turn's job is translation, not judgment. It renders the segment the query identifies into a deployable WHERE clause and offers it; whether the segment is risky is the Backtest's verdict, which the FSM runs next. Drafting an untested candidate endorses nothing — Save is still gated on a Backtest of the exact clause (ADR-0003), so nothing deploys unproven.

The turn declines only for a structural reason about the query, never a risk read of the rows:

1. The query summarizes the whole table and names no segment (a bare count, an average, a distribution with no filter), so there is nothing to filter on.
2. The segment the query selects is defined by the fraud label itself (it filters on `fraud_labels`/`is_fraud`), which a deployed rule can never reference, so nothing deployable remains to carry.

A query that only JOINs `fraud_labels` to *measure* fraud while filtering on transaction attributes still names a deployable segment and drafts. A query that mixes a label filter with deployable predicates declines as a whole rather than dropping the label and silently broadening the segment. The fraud_labels deployability ban (issue #13) stays the hard backstop on the positive path via `GuardedExecutor.validate_clause`. Declining is a stated fact about the query, never a refusal to let the FSM test: the Workbench hand-authoring path (ADR-0003) is always open, so they can write any clause and Backtest it regardless.

## Considered Options

- **Keep `no_rule` as an LLM judgment, tune the prompt to trigger less often.** Rejected: the flaw is that the veto exists at all. A better-tuned veto is still the assistant overruling the FSM's hypothesis, and it still fires on the segment queries the FSM most wants to test.
- **Strip the undeployable predicate and draft the deployable remainder for mixed label queries.** Rejected: dropping `is_fraud = 1` from `is_fraud = 1 AND amount_usd_cents > 50000` silently broadens "fraudulent high-value transactions" to all high-value transactions, committing the FSM to a hypothesis they did not state. Deciding whether the remainder is a meaningful hypothesis is the judgment being removed.
- **Derive the clause deterministically by parsing the query's WHERE.** Rejected for this prototype: translating an arbitrary joined SELECT (a city named in a dimension table becomes a subquery over `transactions`) is what the LLM is good at, and a deterministic SQL parser is disproportionate. The LLM translates; `validate_clause` and the eval suite enforce.
- **Chosen: translate the segment, decline only on structural grounds, let the Backtest judge quality.**

## Trade-offs accepted

- The turn can now draft a clause for a segment that the Backtest later shows catches little fraud. That is intended: the Backtest is where a weak rule is revealed, and an un-backtested candidate endorses nothing.
- "Decline" still exists, so an FSM can still see "no rule from this run", but only for whole-table aggregates and label-scoped queries, worded as a fact about the query. The false decline that motivated this ADR (a segment query refused) becomes the primary regression the rule-quality evals (issue #12) must pin — retargeted from *rewarding* `no_rule` on informational queries to *failing* a decline on any segment-naming query.
- One prompt still does three jobs (summary, translation, the decline decision), carried over from ADR-0006, so a regression in one can leak into the others.
- The wire contract was renamed in the follow-up sweep: the run turn's reply key is `decline` and the run response carries `decline_reason`, matching the new meaning across the drafter, the API, the evals, and the frontend.
