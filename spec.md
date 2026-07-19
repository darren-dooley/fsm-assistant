# Spec: FSM Assistant

Vocabulary below is defined in `CONTEXT.md` (FSM, Insight, Candidate Rule, Rule, Backtest, Score, Cutoff, Segment, Decline, Monitoring, Drift). Decision rationale lives in `docs/adr/`.

## Problem Statement

A Fraud Success Manager (FSM) is a fraud domain expert who protects merchants by finding emerging fraud patterns in transaction data. Their workflow: explore the data, form hypotheses about fraud signals (Insights), validate them against historical labeled data, and translate validated patterns into deployable Rules that block fraudulent payments.

The FSM cannot be required to write SQL. Today every step depends on technical intermediaries: exploring 1.16M transactions requires queries the FSM cannot write, validating a hypothesis requires joining fraud labels and computing metrics by hand, and expressing a pattern as a production-ready SQL WHERE clause is out of reach entirely. The loop is slow, and promising Insights die before they become Rules. Dataset facts that shape the design: 1,159,966 transactions, 777,339 fraud labels (~67% coverage), 1,360 labeled fraud (~0.17% of labeled).

## Solution

An FSM Assistant: a web application where the FSM works in plain English end to end.

- **Explore**: a real conversation about the data. When a turn calls for numbers, the assistant attaches a single read-only SELECT, validated through the same guardrails as execution but not run; the FSM executes it with an explicit **Run** button (ADR-0005). Results always render beside the exact SQL that produced them, with a plain-English summary grounded in the real rows.
- **Promote**: the Run turn also translates the Segment the query names into a Candidate Rule (a WHERE clause over `transactions`, validated for deployability) and attaches it to the run card; it never judges whether the segment is risky, and declines only for structural reasons (ADR-0006, ADR-0007). "Create a Rule" carries the pre-validated draft into the Workbench with no further LLM call.
- **Backtest**: deterministic SQL, no LLM. It reports fraud caught, legitimate transactions blocked, precision, recall, and lift over the base rate, computed only over labeled pre-cutoff transactions, plus a 0-100 Score always shown with its components and the legit-blocked-per-fraud-caught tradeoff.
- **Save**: only a backtested clause can be saved; any edit invalidates the evidence and requires a fresh Backtest. Saved Rules keep the clause and the Backtest snapshot they were approved with, and can be re-backtested, edited, or deleted.
- **Monitor** (P2, designed, unbuilt): saved Rules replayed weekly against the sealed post-cutoff slice, live precision shown against the Backtest snapshot, Drift flagged when it falls materially below.

The UI is a workbench of durable surfaces (ADR-0002, amended by ADR-0003/0004): **Explore** (chat only, every answer showing its SQL), **Rule Workbench** (the sole SQL-authoring surface: an editable WHERE clause, explicit Run-backtest, LLM-free), **Rules** (the saved set with evidence), and a P2 **Monitoring** tab. An eval suite measures NL→SQL accuracy and end-to-end rule quality so a Head of Fraud can judge effectiveness and safety.

## Priorities

Per the assignment's MVP guidance, the named core feature is reliable NL→SQL exploration; unbuilt tiers are outlined in the README.

- **P0 (shipped)**: chat exploration with NL→SQL, the guarded executor, the bounded repair loop, the NL→SQL golden-set eval, the README.
- **P1 (shipped)**: the insight-to-rule transition: drafting in the Run turn, clause validation, Backtest with Score, the save gate, the Rules tab, rule-quality evals.
- **P2 (designed, unbuilt)**: the Monitoring tab, the LightGBM evaluation yardstick, Workbench polish (instant clause preview, Score polish). The monitoring and yardstick designs appear in the README's evaluation section regardless.

## User Stories

1. As an FSM, I want to ask questions about transaction data in plain English, so that I can explore the data without writing SQL.
2. As an FSM, I want to see the answer to my question as a readable table or summary, so that I can interpret results without technical help.
3. As an FSM, I want to see the exact SQL the assistant ran for each answer, so that I (or a colleague) can verify the answer is grounded in the real data.
4. As an FSM, I want to ask follow-up questions that build on earlier ones in the conversation, so that I can iterate toward an Insight without restating context.
5. As an FSM, I want the assistant to tell me when it cannot answer a question from the available data, so that I am not misled by a fabricated answer.
6. As an FSM, I want exploration queries to be strictly read-only, so that I can never damage the transaction data by asking a question.
7. As an FSM, I want long-running or runaway queries to be cut off with a clear message, so that one bad question doesn't hang my session.
8. As an FSM, I want to know that fraud-rate answers are computed only over transactions that actually have fraud labels, so that missing labels don't silently distort my analysis.
9. As an FSM, I want to see the overall base fraud rate as a reference point, so that I can judge whether a pattern is genuinely anomalous.
10. As an FSM, I want to ask the assistant to turn an Insight from our conversation into a Candidate Rule, so that I can move from a pattern to something deployable without writing the WHERE clause myself.
11. As an FSM, I want to describe a rule directly (without prior exploration), so that I can codify hypotheses I already hold.
12. As an FSM, I want the assistant to explain a Candidate Rule's SQL back to me in plain English, so that I can confirm it matches my intent before backtesting.
13. As an FSM, I want every Candidate Rule to be validated as a syntactically correct, safe WHERE clause before it runs, so that malformed or dangerous SQL never reaches the database.
14. As an FSM, I want every Candidate Rule to be backtested before I can save it, so that no Rule enters the rule set without evidence.
15. As an FSM, I want the Backtest to show how many fraudulent transactions the Rule catches (and what fraction of all known fraud that is), so that I understand its benefit.
16. As an FSM, I want the Backtest to show how many legitimate transactions the Rule would block, so that I understand its cost to good customers.
17. As an FSM, I want the Backtest to report precision and recall in plain language, so that I can compare Candidate Rules on a like-for-like basis.
18. As an FSM, I want the Backtest to compare the Rule's hit rate against the base fraud rate, so that I can see the lift the Rule provides.
19. As an FSM, I want backtests to complete in seconds against the full historical dataset, so that iterating on a Candidate Rule feels interactive.
20. As an FSM, I want to tweak a Candidate Rule and re-backtest it, so that I can iterate toward a better trade-off before saving.
21. As an FSM, I want to save a backtested Candidate Rule with a name and description, so that my validated work is captured as a deployable artifact.
22. As an FSM, I want the saved Rule to store the exact WHERE clause and the Backtest evidence it was saved with, so that anyone reviewing the rule set can see its justification.
23. As an FSM, I want to view all saved Rules with their backtest evidence in one place, so that I can manage the rule set as a whole.
24. As an FSM, I want to re-run a Backtest on a saved Rule on demand, so that its evidence is reproducible; checking how it performs on later data is Monitoring's job (stories 42-43).
25. As an FSM, I want to delete a Rule from the rule set, so that stale or superseded Rules don't linger.
26. As an FSM, I want editing a saved Rule to require a fresh Backtest before the change takes effect, so that the rule set never contains unvalidated logic.
27. As an FSM, I want clear error messages in plain English when something goes wrong (bad question, failed query, LLM unavailable), so that I know what to do next rather than being stuck.
28. As an FSM, I want the UI to clearly distinguish data-grounded results from the assistant's own commentary, so that I never mistake speculation for evidence.
29. As a Head of Fraud, I want an evaluation suite that scores the assistant's NL-to-SQL accuracy against a golden question set, so that I can trust the exploration answers.
30. As a Head of Fraud, I want an evaluation of the end-to-end ability to produce Rules that detect labeled fraud, so that I can judge whether the tool actually improves fraud detection.
31. As a Head of Fraud, I want eval results reported as concrete metrics I can track across versions, so that I can see whether changes make the system better or worse.
32. As an engineer, I want the application to run locally from a fresh clone with documented setup steps, so that reviewers can reproduce it easily.
33. As an engineer, I want the LLM provider behind a single injectable interface, so that tests are deterministic and the model can be swapped without touching features.
34. As a Head of Fraud, I want the eval suite to compare the rule set's aggregate detection against the LightGBM yardstick on the post-cutoff holdout, so that I can judge whether the rules are pulling their weight. (P2)
35. As an FSM, I want Backtest results labeled as evidence from explored (pre-cutoff) data, so that I never mistake in-sample numbers for live performance.
36. As a Head of Fraud, I want the eval suite to report the trained model's detection metrics on the post-cutoff holdout, so that I have a yardstick for judging the rule set's performance. (P2)
37. As an engineer, I want the baseline model trained by a seeded, reproducible script, so that the benchmark is auditable and rebuildable from the provided data. (P2)
38. As an FSM, I want to start a blank Candidate Rule in the Rule Workbench and write its WHERE clause directly, so that I (or a technical colleague) can author a rule without going through chat.
39. As an FSM, I want to edit a Candidate Rule's WHERE clause directly in the Rule Workbench, so that I can make precise changes to the deployable artifact.
40. As an FSM, I want quick feedback on a clause as I edit it, so that I can feel out a rule before saving. (Shipped as the explicit Run-backtest action; the instant preview is P2 polish, per ADR-0003 as amended.)
41. As an FSM, I want every Backtest to produce a 0-100 Score shown alongside its component metrics, so that I can compare and rank Rules at a glance without a single number hiding the tradeoff.
42. As an FSM, I want a Monitoring tab showing each saved Rule's weekly precision on post-cutoff data against its Backtest expectation, so that I can spot decaying rules. (P2)
43. As an FSM, I want Drift flagged when a Rule's live precision falls materially below its Backtest snapshot, so that decayed rules get attention rather than lingering. (P2)
44. As an FSM, I want failed SQL translations repaired automatically (the error fed back to the model, bounded retries), so that transient generation errors don't surface as dead ends.

## Implementation Decisions

- **Architecture**: a Python FastAPI backend (uv-managed, committed `uv.lock`) serving a JSON API, and a Vite React TypeScript frontend. SQLite is the only datastore. The provided `data/data.db` is opened read-only; application state (saved Rules, the derived label cache) lives in app-owned databases under `backend/var/`, so the provided dataset stays pristine.
- **Global cutoff (T)**: one date (default 2019-09-01) splits the dataset. Exploration, previews, and Backtests see only pre-T data (~80% of transactions); the post-T slice is sealed for Monitoring and the eval holdout, so Rules are judged on data nobody saw while authoring them. Backtest output is labeled in-sample and optimistic by construction. (ADR-0004)
- **Guarded executor**: the only path to the provided dataset. Per-query read-only connections; a SQLite authorizer allowing SELECT/read/function only (no writes, DDL, PRAGMA, ATTACH); temp views shadowing `transactions`/`fraud_labels` with their pre-T slice so the holdout is sealed even against qualified table names; one statement per question; a hard row cap; a progress-handler timeout.
- **LLM access**: the OpenAI API behind a small injectable client interface chosen at app construction; tests inject a deterministic fake. Model id is configuration.
- **Conversational Explore, run on request**: the assistant replies conversationally and attaches a SQL proposal only when the turn calls for data. Proposals are validated (compile-only, same guardrails, same bounded ≤3-attempt repair loop) but never executed; the FSM's explicit Run click executes through the guarded executor. This is deliberately not an autonomous agent: the model only translates, the human drives the loop. (ADR-0005)
- **Drafting in the Run summary turn**: the one LLM call that already sees the question, the SQL, and the real rows also returns either a Candidate Rule (`{clause, name, description}`) or a structural decline. The clause is validated inline (`validate_clause`: guardrails plus a deployability ban on reading `fraud_labels`, since production rules see unlabeled traffic) inside the same repair loop. The Workbench makes no LLM calls: it pre-fills, lets the FSM edit, backtests, and saves. (ADR-0006)
- **Translation, never judgment**: the drafter renders the Segment the query names into a clause; whether the segment is risky is the Backtest's verdict. It declines only when the query names no deployable segment (a whole-table aggregate, or a segment defined by the fraud label itself), worded as a fact about the query, never a risk read of the rows. (ADR-0007)
- **Rule representation**: exactly the assignment's definition, a valid SQL WHERE clause over `transactions`, subqueries permitted. Validation wraps the clause in the same FROM shape the Backtest uses, so "valid" and "backtestable" cannot drift apart.
- **Backtest engine**: deterministic SQL, no LLM. Metrics computed only over labeled transactions (~67% coverage; the exclusion is counted and reported): flagged, fraud caught (and share of known fraud), legitimate blocked, precision, recall, lift over the ~0.17% base rate. Raw counts always travel beside ratios, which alone mislead at this imbalance. The 0-100 Score is a fixed weighted blend of precision, recall, and capped lift (weights in code, not configuration), never shown without its components.
- **Candidate Rule lifecycle**: draft → backtest → save. Saving requires a Backtest of the exact clause being saved; any edit invalidates prior evidence. Saved Rules persist clause, name, description, and the approving snapshot.
- **API contract** (as built): `POST /api/explore` (converse, propose SQL), `POST /api/explore/run` (execute + summarize + draft), `POST /api/rules/backtest`, rule-set CRUD (`GET/POST /api/rules`, `PUT/DELETE /api/rules/{id}`, `POST /api/rules/{id}/backtest`), `GET /api/meta` (dataset facts, base rate, cutoff). The frontend is a thin client over these.
- **UI shape**: the workbench tabs per ADR-0002/0003. Explore is chat-only; there is no raw SELECT editor anywhere. The Rule Workbench owns all SQL authoring (WHERE clauses only), reachable from a run card's draft, from editing a saved Rule, or blank; backtesting is an explicit action.
- **LightGBM evaluation yardstick (P2, design-only)**: a seeded gradient-boosted classifier trained on pre-cutoff labeled data, reported only in the eval suite (recall at fixed false-positive budgets, PR-AUC on the post-cutoff holdout) beside the rule set's aggregate metrics. No product surface: it never gates saves, suggests signals, or blocks transactions. (ADR-0001)
- **Hallucination mitigations** (the GenAI-limitations stance): the LLM only ever produces SQL or prose about SQL; every number shown comes from executing real queries; generated SQL is always displayed; execution requires an explicit human action; rules cannot be saved without deterministic Backtest evidence; the database is read-only to anything LLM-generated.

## Testing Decisions

- **One primary seam**: the backend HTTP API, driven in-process. Every behavior (exploration turns, run summaries and drafts, backtest metrics, the save gate, rule CRUD) is asserted through requests and responses; internals stay free to refactor.
- **One supporting seam**: the injectable LLM client. Tests supply a fake returning scripted replies, making the whole suite deterministic and offline. No other test doubles.
- **What makes a good test here**: external behavior only. Backtest tests use a small fixture database with hand-computable labels, so expected precision/recall are verifiable arithmetic, not snapshots.
- **Guardrail tests at the same seam**: a scripted LLM emitting non-SELECT, multi-statement, post-cutoff-reading, or label-reading SQL must yield a safe error response, never execution.
- **Eval suite separate from the test suite**: it exercises the real LLM through the same in-process HTTP seam and reports metrics, not pass/fail. The NL→SQL golden set scores by execution-result match (never SQL string comparison), with refusal scoring on unanswerable questions, SQL shape lints for known-bad label joins, and a `--runs` mode for flake measurement. Rule-quality evals replay the FSM's path on a hand-computed fixture database; the headline regression metric is the false-decline rate on segment-naming patterns (ADR-0007).
- **Online evaluation is a README deliverable regardless of build progress**: drift monitoring against Backtest snapshots, shadow-mode rollout, product and safety metrics. See the README's evaluation section.

## Out of Scope

- Deploying Rules to any production blocking system; the saved rule set is the end artifact.
- Authentication or multi-user concerns: single local FSM user.
- Any product surface for the LightGBM model (ADR-0001).
- Hyperparameter search beyond a sensible seeded baseline.
- Real-time scoring or streaming ingestion; the dataset is static and historical.
- Rule versioning/approval workflows beyond draft → backtest → save.
- Modifying or enriching the provided dataset.
- A raw SQL SELECT editor; ad-hoc aggregate SQL is a job for a real SQL client against the read-only database (ADR-0003).
- Result-level verification of exploration answers beyond always showing the executed SQL.
- Autonomous agent loops: the LLM never chooses actions; the bounded repair loop is the only iteration.
