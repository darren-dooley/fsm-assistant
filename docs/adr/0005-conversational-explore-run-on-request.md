# Explore is a real conversation; queries run only on request

Amends ADR-0002/0003, which made Explore a chat where every message was eagerly translated to SQL and executed. Using the built P0 surfaced the problem: the eager pipeline treats every input as a data question. A greeting, a clarifying question, or thinking out loud all triggered a translation attempt and a query execution, and the FSM had no way to talk to the assistant about the data before committing to a query. Execution was also implicit — the FSM never chose to spend a query, and the repair loop could burn attempts executing SQL the FSM never wanted run.

The resolution: split proposing from running. Explore becomes a genuine back-and-forth chat. The assistant replies conversationally by default and attaches a SQL proposal only when the turn calls for data. Proposed SQL is validated (compile-only, same guardrails as execution, same bounded ≤3-attempt repair loop) but not executed; it renders with a Run button. Running is always FSM-initiated: it executes through the guarded executor, shows the result table, offers "Create a Rule from this", and feeds a plain-English summary of the real rows back into the conversation so follow-ups can build on it.

Downstream simplification: with query proposals now explicit artifacts in the chat, the Rule Workbench's "describe the rule in plain English" box is redundant — conversation happens in Explore. "Create a Rule" drafts the WHERE clause from the chat plus the query that was run; in the workbench the clause itself is the only editable rule text (name/description remain rule metadata). The blank-workbench entry point (PRD story 11) is now a hand-written clause.

## Considered Options

- **Keep eager execute, add an intent classifier** — pre-classify each message as chit-chat vs data question. Rejected: two model calls per turn to preserve a flow that still executes without the FSM's say-so.
- **Auto-run with an undo/cancel** — keep execution implicit but interruptible. Rejected: the queries are cheap enough that the cost isn't the point; the point is that the FSM drives the loop, and an explicit Run states that plainly.
- **Chosen — propose-validate-run**: conversational replies by default, validated SQL proposals when warranted, execution and summarization only behind the Run button.

## Trade-offs accepted

- Answering a data question now takes one more click. The click is the feature: it is the FSM's decision that turns a proposal into evidence.
- The golden-set eval scores a two-step path (propose, then run) instead of one endpoint, and "refusal" now means "declined to propose SQL" rather than an explicit refusal object.
- The assistant's reply on a proposal turn describes what the query computes without seeing results; the grounded summary arrives only after Run. Between those two moments the chat contains a claim-free description, which reads slightly drier than the old instant answer.
