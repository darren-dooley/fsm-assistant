# WHERE-shaped SQL lives in the Rule Workbench; Explore is chat-only

Amends ADR-0002, which gave Explore a Chat/SQL toggle (NL→SQL chat plus a raw SELECT editor). Working with the prototype surfaced two problems. The toggle implied a continuity that didn't exist: the two modes were separate tools with separate state, and there was no defined answer to what the SQL editor should show after a chat conversation had iterated through several queries. And the Rule Workbench already is a query surface — edit the WHERE clause, see matching rows, backtest, repeat — so "where does SQL go" was really "where do full SELECTs go."

The resolution: split by query shape, not input modality. A WHERE clause has defined semantics for preview, backtest, and save (it is the deployable artifact); an arbitrary SELECT has none of the three. So WHERE-shaped SQL belongs to the Rule Workbench, and aggregate-shaped querying stays in Explore as chat only.

> Amended (2026-07-19): aligned to the shipped Workbench. The "instant preview on every clause edit" described below was **not built**. The Workbench validates and evaluates a clause only when the FSM clicks **Run backtest** (an explicit action), and Save is gated on a backtest of the exact current clause. This matches ADR-0005's principle that the FSM drives the loop — no query fires without a deliberate action — and removes the need for the debounced COUNT-preview the trade-offs below anticipated. Read "instant preview" throughout this ADR as "the Run-backtest action". The shape-split decision itself (WHERE-shaped SQL in the Workbench, chat-only Explore) shipped as written.

## Considered Options

- **Keep the toggle, split the states** — chat and SQL modes as explicitly separate tools, with a per-exchange "open in SQL editor" handoff. Rejected: a stateful two-mode Explore is more to build, and its main beneficiary (a technical user auditing the assistant) is already served by the read-only SQL shown on every chat answer.
- **Full SQL editing in the Workbench** — move the SELECT editor into the Rule Workbench. Rejected: an aggregate SELECT can't be backtested or saved, so the workbench would need two internal modes (queries that can complete its loop and queries that can't), and an editable source query alongside an editable clause leaves it ambiguous which one is the rule.
- **Chosen — cut Explore's SQL mode**: Explore is chat-only; every answer still shows the SQL it ran, read-only, for audit. The Rule Workbench is the sole SQL-authoring surface: the editable WHERE clause, a blank-rule entry point (from the Rules tab or the empty workbench) for users who want to write a clause directly, and an explicit **Run backtest** action that validates the clause and returns its full evidence (transactions matched, known fraud among labeled matches, precision/recall/lift vs base). [As built — the earlier plan for an automatic preview on every edit was dropped; see the 2026-07-19 amendment.]

## Trade-offs accepted

- Technical users lose ad-hoc aggregate SQL inside the product. Chat covers those questions and exposes its SQL; anything beyond that is a job for a real SQL client against the read-only database.
- "Start in SQL" now means starting a rule, not a query. That matches the case power users actually wanted (writing a clause directly) but drops free-form SELECT experimentation.
- Backtesting is an explicit action rather than a live preview, so the FSM gets no feedback while typing a clause — they click **Run backtest** to see evidence. This was the accepted simplification over the originally-planned instant preview (which would have needed debouncing and a cheap COUNT-shaped query to stay interactive against 1.16M rows); it also keeps the "FSM drives the loop" invariant from ADR-0005.
