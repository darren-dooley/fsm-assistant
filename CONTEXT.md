# Ubiquitous Language — FSM Assistant

## Terms

### FSM (Fraud Success Manager)
The user of this product. A fraud domain expert, not assumed to be technical
(cannot be required to write SQL). Their workflow: explore transaction data,
form hypotheses about fraud signals, validate them against historical data,
and translate validated patterns into deployable rules.

### Rule
A valid SQL `WHERE` clause over the `transactions` table (per the assignment
definition, e.g. `amount_usd_cents > 100000 AND card_id IN (...)`). The
deployable end-artifact of the FSM workflow. A rule is what production systems
would use to block payments. Any valid boolean WHERE expression is permitted,
including subqueries.

### Insight
A pattern an FSM has observed in the data and hypothesizes to be a fraud
signal (e.g. "online transactions over $500 between 2–5am have an elevated
fraud rate"). Insights are produced by exploration; a promoted insight becomes
a Candidate Rule.

### Candidate Rule
A drafted Rule that has been backtested but not yet saved by the FSM. Only a
saved Rule counts as part of the rule set.

### Backtest
Evaluation of a Rule against historical labeled transactions, producing
evidence of its quality (how much fraud it catches, how many legitimate
transactions it would block). Every Candidate Rule is backtested before an
FSM can save it.

### Score
A 0–100 blend of a Rule's Backtest evidence (precision, recall, lift) used to
rank Rules at a glance. Always shown alongside the underlying metrics and the
legitimate-blocked-per-fraud-caught tradeoff, never alone.

### Cutoff (T)
The single date that splits the historical dataset. Exploration, previews,
Backtests, and model training see only pre-T transactions; the post-T slice
is sealed for Monitoring and the eval suite, so Rules are judged on data
nobody saw while authoring them.

### Segment
A filterable subset of transactions that a query identifies (a city's
merchants, a transaction type, an amount band). The Run turn translates the
segment a query names into a Rule clause; whether the segment is risky is
the Backtest's verdict, never the drafter's (ADR-0007).

### Decline (structural)
The Run turn's stated reason that a run carries no Candidate Rule, permitted
only when the query names no deployable Segment: it summarizes the whole
table, or its segment is defined by the fraud label itself. A fact about the
query, never a risk verdict on the rows.

### False Decline
A Decline on a segment-naming query: a veto of the FSM's hypothesis before
the Backtest can test it. The headline regression the rule-quality evals
track (ADR-0007).

### Monitoring
Ongoing evaluation of saved Rules against the sealed post-cutoff slice,
bucketed by week, as a stand-in for live traffic.

### Drift
A saved Rule's post-cutoff performance falling materially below the Backtest
snapshot it was saved with. Surfaced by Monitoring.
