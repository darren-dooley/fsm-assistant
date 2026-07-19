# Three-tab Rule Workbench for the FSM interface, not a pipeline board

The FSM's interaction surface for the explore → Insight → Candidate Rule → Backtest → save workflow was prototyped five ways (all fixtures, no backend or LLM). We settled on the last, a three-tab Workbench modeled loosely on Stripe Radar's rules + backtesting + risk-score product.

> Amended by ADR-0003: Explore's Chat/SQL toggle was dropped. Explore is chat-only, and the Rule Workbench is the sole SQL-authoring surface (with a blank-rule entry point and an explicit Run-backtest action; the "instant preview on every edit" first proposed in ADR-0003 was not built — see that ADR's 2026-07-19 amendment).
>
> Amended by ADR-0004: a fourth durable surface (Monitoring, P2) was added, and the open gaps below (out-of-time holdout, post-deploy monitoring) are resolved by the global cutoff design.

## Considered Options

- **A — Chat-first** — the whole workflow is one conversation stream; rule and backtest cards appear inline; the rule set lives in a drawer. Rejected: the conversation buries the rule set and backtest evidence, the artifacts an FSM returns to most.
- **B — Split workspace** — chat on the left, a persistent rule workspace on the right. Rejected: two dense panes competing for attention; the rule set and a single candidate fight for the same right-hand column.
- **C — Pipeline board** — four columns (Explore | Insights | Candidate Rule | Rule Set), work moving left to right. Rejected: gives four transient workflow stages equal permanent real estate, when only three surfaces (explore, author/validate, manage) are durable.
- **D — Tabbed pipeline** — C's four stages as top tabs that unlock as the workflow advances. Rejected: the linear unlocking over-guides a loop that is iterative in practice, and you lose C's at-a-glance view of every stage without gaining a durable structure.
- **Workbench (chosen)** — three tabs matching the three durable surfaces: **Explore** (Chat/SQL toggle — NL→SQL with the query shown for auditability, or a raw editor), **Rule Workbench** (editable WHERE clause, backtest with a plain-English tradeoff and a 0–100 score), **Rules** (saved set, each carrying its score). Separates the three things an FSM actually revisits rather than the transient pipeline stages, and the SQL/backtest surfaces double as the hallucination mitigation (audit the query, judge the evidence).

## Trade-offs accepted

- We lose C's at-a-glance view of every stage's state at once; the three tabs show one surface at a time.
- The workflow is no longer linearly guided the way D's unlocking tabs were — the FSM must navigate the loop themselves.
- Scoring collapses precision/recall/lift/cost into one number; mitigated by keeping the components (and the legit-per-fraud cost) visible alongside it, never the score alone.
- Fixtures only so far. Open gaps for the real build: backtest on an out-of-time holdout (not the explored labels), a post-deploy monitoring surface, and result-level rather than SQL-level verification for the non-technical user.
