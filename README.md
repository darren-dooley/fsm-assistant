# FSM Assistant

A GenAI workbench for a Fraud Success Manager: explore 1.16M transactions in
plain English, promote a pattern to a fraud rule, and back every rule with
deterministic evidence. The LLM only ever writes SQL or prose about SQL;
every number on screen comes from executing real queries, and no rule can be
saved without a Backtest.

Product spec in [spec.md](spec.md), domain vocabulary in
[CONTEXT.md](CONTEXT.md), decision records in `docs/adr/`.

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 20+. The provided
dataset (`data/data.db`) is checked in.

```sh
cd backend && uv sync
OPENAI_API_KEY=sk-... uv run fsm-assistant   # API on :8000; first start builds a small cache db
```

```sh
cd frontend && npm install && npm run dev    # UI on :5173, proxies /api to the backend
```

Optional env: `OPENAI_MODEL` (default `gpt-5-nano`), `FSM_PORT` (default
`8000`; start the frontend with the same value), `FSM_CUTOFF` (`2019-09-01`),
`FSM_ROW_LIMIT` (`200`), `FSM_QUERY_TIMEOUT_MS` (`5000`).

## Architecture

```
┌────────────────────────────── React UI ───────────────────────────────┐
│  Explore            Rule Workbench          Rules        Monitoring   │
│  chat · NL→SQL      edit · backtest · save  saved set    P2 · unbuilt │
│  propose + Run      (LLM-free)              + evidence   (disabled)   │
└───────────────────────────────────┬───────────────────────────────────┘
                                    │ JSON /api
┌───────────────────────────────────▼───────────────────────────────────┐
│ FastAPI · create_app(settings, llm_client)                            │
│   POST /explore        converse; propose SQL (validated, never run)   │
│   POST /explore/run    execute · grounded summary · draft / decline   │
│   POST /rules/backtest deterministic metrics + Score, no LLM          │
│   /rules CRUD          save gate: backtest of the exact clause        │
├───────────────────────────────────────────────────────────────────────┤
│ Translator            SummaryDrafter          BacktestEngine          │
│ chat · repair ≤3      run turn: summary +     labeled-only metrics    │
│                       rule draft or decline   + Score     RuleStore   │
├───────────────────────────────────────────────────────────────────────┤
│ GuardedExecutor: read-only authorizer · pre-cutoff temp views ·       │
│ row cap · timeout        LLMClient (Protocol): OpenAI client or fake  │
├───────────────────────────────────────────────────────────────────────┤
│ data.db (provided · opened mode=ro)   cache.db + app.db (app-owned)   │
└───────────────────────────────────────────────────────────────────────┘
```

The FSM drives every step: the assistant proposes a validated query, the
explicit **Run** button executes it, the run's summary turn drafts a
Candidate Rule (or states a structural decline), and the Workbench backtests
and saves with no LLM involved.

One date T splits the dataset (ADR-0004), shared by everything:

```
──────────── pre-T · visible (~80%) ────────────┬────── post-T · sealed ──────
 explore · backtests · rule authoring           │ Monitoring (P2) · eval
                                 T = 2019-09-01 │ holdout
```

A rule is authored and backtested on the same pre-T slice, so its Backtest is
optimistic by construction; the UI says so, and post-T is the honest measure.

## Folder structure

```
backend/src/fsm_assistant/
  api.py              FastAPI app; create_app(settings, llm_client)
  config.py           env-driven settings
  guarded.py          GuardedExecutor: the only path to the dataset
  translator.py       Explore chat + bounded repair loop
  summary_drafter.py  run turn: grounded summary + rule draft / decline
  backtest.py         deterministic Backtest engine + 0-100 Score
  rule_store.py       saved rule set (app-owned db)
  app_db.py           app-owned databases; derived pre-T label cache
  llm.py              injectable LLM client (OpenAI / fake)
  evals/              fsm-eval: golden-set + rule-quality suites
frontend/src/
  App.tsx             tabs + masthead dataset facts
  Explore.tsx         chat, SQL proposals, run cards
  Workbench.tsx       clause editor, backtest evidence, save gate
  Rules.tsx           saved set; edit / re-backtest / delete
  evidence.tsx        Score band + evidence panel
  api.ts · handoff.ts · score.ts
```

## GenAI risk stance

- The LLM produces SQL or prose about SQL, never data. Every figure shown is
  the result of executing a real query.
- Guardrails are application-enforced, not prompt-enforced: a SQLite
  authorizer permits reading only (no writes, DDL, PRAGMA, ATTACH), one
  statement per question, hard row cap, hard timeout, and temp views that
  seal the post-cutoff slice even against qualified table names.
- Proposed SQL is always displayed and executes only on an explicit click;
  results render beside the query that produced them.
- Invalid SQL is repaired by feeding the error back to the model, at most 3
  attempts, every attempt re-guarded. Unanswerable questions are declined,
  and the evals measure the refusal rate.
- A rule draft translates the segment the FSM's query names; it never judges
  risk (ADR-0007). The deterministic Backtest is the verdict, and nothing is
  saved without one.
- Rules cannot read `fraud_labels` (production traffic is unlabeled),
  enforced by clause validation, not the prompt.
- No autonomous agent: the model never chooses actions; the bounded repair
  loop is the only iteration.

## Tests

```sh
cd backend && uv run pytest   # 104 tests, offline, no key needed
```

The suite drives the HTTP API in-process with a scripted fake LLM. Guardrail
behavior (SELECT-only, row cap, timeout, sealed holdout, the save gate) is
asserted at the same seam; backtest tests use a fixture database whose
expected metrics are hand-computable arithmetic.

## Evaluation

### Offline (built)

Both suites drive the real LLM through the same in-process HTTP seam the
tests and product use, score by execution results (never SQL strings), and
write a metrics report to `backend/var/evals/*.json`: rates to track across
versions, not pass/fail.

```sh
cd backend
OPENAI_API_KEY=sk-... uv run fsm-eval                    # NL→SQL golden set
OPENAI_API_KEY=sk-... uv run fsm-eval golden --runs 10   # flake measurement
OPENAI_API_KEY=sk-... uv run fsm-eval rules              # rule quality
```

**NL→SQL golden set** (P0): 14 questions replayed through the propose-then-run
path. Expected answers are derived by running known-good SQL through the same
guarded executor; deliberately unanswerable questions must be declined; shape
lints catch the label-join mistakes result-matching can't name (a fraud rate
diluted by unlabeled rows, a needless `fraud_labels` join). Latest
(gpt-4o-mini, 10 runs): **97.3% mean execution match** (min 90.9%), **100%
refusal on unanswerable questions, 0 false refusals**, 0.9% shape-violation
rate, mean repair depth 1.1.

**Rule quality** (P1): 12 known patterns replayed through the run turn's
drafter, each drafted clause backtested on a fixture database with
hand-computed expected counts, so any faithful phrasing lands on the same
rows. The headline metric is the false-decline rate: a decline on a
segment-naming query vetoes a hypothesis before the Backtest can test it
(ADR-0007). Latest (gpt-4o-mini): **0% false declines**, **8/8 metric
patterns match** the hand-computed counts, 4/4 structural patterns correctly
decline, draft repair depth 1.0.

### Online (designed, unbuilt)

How we would prove effectiveness and safety in production:

- **Drift monitoring**: each saved Rule's live precision tracked weekly
  against the Backtest snapshot it was saved with; alert when materially
  below. The P2 Monitoring tab is its in-product expression, with the sealed
  post-T slice standing in for live traffic.
- **Shadow mode**: new Rules tag but don't block until live precision
  supports enforcement.
- **Product metrics**: FSM time from first question to saved Rule; share of
  drafts that survive Backtest.
- **Safety metrics**: guardrail rejection rate, repair-loop depth
  distribution, refusal rate on unanswerable questions.

### Model yardstick (designed, unbuilt)

A seeded LightGBM classifier trained on pre-T labels, reported only in the
eval suite (PR-AUC, recall at fixed false-positive budgets on the post-T
holdout) beside the rule set's aggregate detection: a yardstick for the Head
of Fraud, with no product surface (ADR-0001).

## Not built (P2 outlines)

- **Monitoring tab** (ADR-0004): weekly post-T replay per saved Rule against
  its snapshot, Drift flagged; the tab renders disabled in the UI.
- **LightGBM yardstick** (ADR-0001): above.
- **Workbench polish**: instant clause preview while typing (debounced
  COUNT-shaped query) and Score presentation polish; today backtesting is an
  explicit action, which also keeps the FSM-drives-the-loop invariant.

## Design decisions

- **Guarded executor as the kernel**: one path to the data; safety is code,
  not prompt.
- **Propose, then Run** (ADR-0005): the FSM's click turns a proposal into
  evidence; no query executes without a deliberate action.
- **Draft in the run turn** (ADR-0006): the one LLM call that sees the real
  rows drafts the rule; the Workbench stays LLM-free and instant.
- **Translate, never judge** (ADR-0007): declines are structural facts about
  the query; the Backtest judges risk. False declines are the headline eval
  regression.
- **One global cutoff** (ADR-0004): pre-T visible, post-T sealed for
  monitoring and evals.
- **WHERE-shaped SQL only in the Workbench** (ADR-0003): Explore is
  chat-only; the deployable artifact is the only authorable SQL.
- **Injectable LLM client**: deterministic offline tests; model id is config.
- **Evals score denotations**: execution-result match and hand-computed
  fixtures, never SQL string comparison.
- **LightGBM as eval yardstick only** (ADR-0001): no model in the product.
