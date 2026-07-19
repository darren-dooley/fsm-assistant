# LightGBM for the fraud detection baseline, not a GNN

> Amended (2026-07-18): the model's role is reduced to a P2 evaluation yardstick. The authoritative assignment brief names NL→SQL exploration as the sole MVP core feature and lists no fraud-detection system, so the model has no product surface: the in-product Backtest benchmark line and the suggested-signals feature are cut. (Suggested signals also clashed with CONTEXT.md, which reserves Insight for FSM-observed patterns, and global feature importances carry no direction or threshold.) The intended P2 role is: train on pre-cutoff data and report only in the eval suite, on the sealed post-cutoff holdout (see ADR-0004), beside the rule set's aggregate metrics as context for the Head of Fraud. The LightGBM-over-GNN reasoning below stands for what remains.

> Amended (2026-07-19): aligned to the shipped prototype — this model is **design-only and unbuilt**. There is no training code, no model artifact, and no eval-suite reporting in the repo; `backend/pyproject.toml` declares no `lightgbm`/`scikit-learn` dependency, and the two eval suites that do exist (NL→SQL golden set, rule quality) contain no model. Read this ADR as the decision of record for if/when the P2 yardstick is built, not as a description of shipped code. Everything below is retained rationale.

The FSM Assistant includes a trained model whose role is advisory: benchmark Rules in Backtest output and surface signals in plain English as suggested Insights. We chose a LightGBM gradient-boosted tree classifier over a graph neural network, even though the schema (users → cards → transactions → merchants) forms a natural entity graph.

## Considered Options

- **LightGBM (chosen)** — the standard for medium-sized tabular data; sample-efficient at our label volume (1,360 positives, ~0.17% of labeled rows); trains in seconds on CPU with a fixed seed, so the benchmark is reproducible; feature importances translate directly into plain-English signals an FSM can express as a SQL WHERE clause Rule, which is the product's deployable artifact.
- **GNN (rejected)** — earns its complexity when fraud is relational (rings, collusion, shared entities across accounts) and neighborhood structure carries signal no single row reveals. Rejected because: too few positives to train well at this scale; embeddings don't map to anything a Rule can express, so its insights would be undeployable in the product's own terms; and it adds graph construction, sampling, and GPU dependence against the reproducibility requirement.

## Trade-offs accepted

- We give up the ability to detect genuinely relational fraud patterns (ring/collusion structure) that only graph methods surface.
- Mitigation available without reversing this decision: engineered relational features fed to LightGBM (per-card velocity, distinct merchants per card per day, leakage-safe entity history rates). These capture much of the relational signal while staying explainable. They are internal to the training script, so they can be added later without changing the API, the model artifact interface, or the product surface.
- Revisit if the label set grows substantially or the product's detection role changes from advisory benchmark to primary blocker.
