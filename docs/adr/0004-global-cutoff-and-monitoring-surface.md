# A global cutoff date, a sealed holdout, and a Monitoring surface

The assignment's Evaluation Mindset criterion asks for thoughts on both offline and online metrics, and ADR-0002 had left two open gaps: backtests ran on the same labels the FSM explored, and there was no post-deploy surface. Both are resolved with one time concept. A single cutoff date T splits the static historical dataset: everything before T is the world the FSM (and the model) can see; everything after T simulates live traffic.

## Decision

- **Global cutoff T**, shared with the LightGBM train/test split. One date, one concept, reused everywhere.
- **The holdout is sealed.** Exploration, clause Backtests, and (design-only, per ADR-0001) model training only ever see pre-T data. If exploration could touch post-T data, a rule tuned on it would show flattering, meaningless monitoring results.
- **Backtests are labeled in-sample evidence.** A rule is authored by looking at pre-T data and backtested on pre-T labels, so its Backtest is optimistic by construction. The UI says so; post-T results are the honest measure.
- **A Monitoring surface (P2)** — a fourth tab: each saved Rule is replayed against the post-T slice bucketed by week, its live precision shown against the Backtest snapshot it was saved with, and drift flagged when live performance falls materially below the snapshot. If unbuilt in the time budget, the design ships as the README's online-evaluation section (the fallback the assignment itself prescribes).

## Considered Options

- **Design-only README section** — describe online metrics without any in-product expression. Rejected as the primary plan: a specced surface with a README fallback demonstrates the evaluation mindset more concretely at little extra spec cost.
- **Per-rule deploy dates** — each rule's save moment maps to its own point on the dataset timeline. Rejected: every rule needs its own split, backtest windows vary per rule, and mapping wall-clock save times onto the historical timeline is arbitrary.
- **Replay scrubber** — a user-advanced simulated "today" progressively revealing post-T data. Rejected: the best demo of drift emerging, but the most build effort and a nonstandard concept to explain.
- **Full data in Explore with only backtest/monitoring split** — rejected: breaks the seal, see above.

## Trade-offs accepted

- The FSM explores a smaller dataset (pre-T only). Acceptable: the pre-T slice still contains the large majority of labeled history.
- Amends ADR-0002's "three durable surfaces" claim: Monitoring is a fourth. The variant analysis (A–D rejected) is unaffected.
- The drift threshold ("materially below") is left for implementation; it should be stated on the Monitoring surface rather than hidden.
- Monitoring is P2: behind the assignment's named MVP (NL→SQL) and the insight-to-rule loop.
