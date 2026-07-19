"""The Backtest engine: deterministic SQL, no LLM in the loop.

A Backtest evaluates a Rule's WHERE clause over the pre-cutoff transactions the
guarded executor exposes, joined to fraud labels, and reports how much fraud
the clause catches against how many legitimate payments it blocks. Metrics are
computed only over labeled transactions (roughly a third are unlabeled; they
are excluded and the exclusion is reported), and raw counts always travel
beside the ratios because at a ~0.17% base fraud rate percentages alone
mislead (PRD stories 15-18, 35, 41).

Output is in-sample evidence: the clause is judged on the same pre-cutoff slice
an FSM could have explored, never on the sealed post-cutoff holdout.
"""

from dataclasses import asdict, dataclass

from .guarded import RULE_EVAL_FROM, GuardedExecutor

# Score weights (in code, not configuration, per the PRD). Precision and recall
# carry the Rule's own quality; lift rewards concentration above the base rate
# but saturates at LIFT_TARGET so a single spectacular-lift clause can't drown
# out a poor precision/recall tradeoff. A Rule that flags nothing labeled has no
# evidence of catching fraud and scores 0.
PRECISION_WEIGHT = 0.4
RECALL_WEIGHT = 0.4
LIFT_WEIGHT = 0.2
LIFT_TARGET = 10.0

EVIDENCE_BASIS = "in-sample (pre-cutoff)"


@dataclass(frozen=True)
class BacktestResult:
    # Raw counts (always shown beside ratios).
    flagged_total: int  # transactions the clause matches, labeled or not
    flagged_labeled: int  # of those, the ones carrying a fraud label
    flagged_unlabeled: int  # the excluded matches, reported for honesty
    fraud_caught: int  # true positives: labeled-fraud matches
    legit_blocked: int  # false positives: labeled-legit matches
    labeled_total: int  # size of the labeled pre-cutoff slice
    fraud_total: int  # all known fraud in that slice
    # Ratios.
    precision: float  # fraud_caught / flagged_labeled
    recall: float  # fraud_caught / fraud_total (== share of known fraud)
    base_rate: float  # fraud_total / labeled_total
    lift: float  # precision / base_rate
    legit_blocked_per_fraud_caught: float | None  # the tradeoff; None if 0 caught
    # The headline, never shown without the components above.
    score: float  # 0-100
    evidence_basis: str = EVIDENCE_BASIS

    def as_dict(self) -> dict:
        return asdict(self)


def compute_score(precision: float, recall: float, lift: float) -> float:
    """The 0-100 Score: a weighted blend of precision, recall, and lift, with
    lift normalized against LIFT_TARGET. Kept simple and linear so its value is
    verifiable arithmetic, not a fitted black box."""
    lift_norm = min(lift / LIFT_TARGET, 1.0)
    blended = PRECISION_WEIGHT * precision + RECALL_WEIGHT * recall + LIFT_WEIGHT * lift_norm
    return round(100 * blended, 1)


class BacktestEngine:
    def __init__(self, executor: GuardedExecutor):
        self._executor = executor

    def run(self, clause: str) -> BacktestResult:
        """Backtest a validated WHERE clause. Raises GuardedQueryError if the
        clause is malformed, unsafe, or times out — callers validate first for a
        clean error, but the engine never trusts an unchecked clause."""
        self._executor.validate_clause(clause)
        labeled_total, fraud_total = self._dataset_totals()
        flagged_total, flagged_labeled, fraud_caught, legit_blocked = self._flagged(clause)

        precision = fraud_caught / flagged_labeled if flagged_labeled else 0.0
        recall = fraud_caught / fraud_total if fraud_total else 0.0
        base_rate = fraud_total / labeled_total if labeled_total else 0.0
        lift = precision / base_rate if base_rate else 0.0
        tradeoff = legit_blocked / fraud_caught if fraud_caught else None

        return BacktestResult(
            flagged_total=flagged_total,
            flagged_labeled=flagged_labeled,
            flagged_unlabeled=flagged_total - flagged_labeled,
            fraud_caught=fraud_caught,
            legit_blocked=legit_blocked,
            labeled_total=labeled_total,
            fraud_total=fraud_total,
            precision=precision,
            recall=recall,
            base_rate=base_rate,
            lift=lift,
            legit_blocked_per_fraud_caught=tradeoff,
            score=compute_score(precision, recall, lift),
        )

    def _dataset_totals(self) -> tuple[int, int]:
        # This query injects no user clause, so it keeps the `t` alias; only
        # `_flagged` must drop it to match validation's statement shape.
        result = self._executor.execute(
            "SELECT COUNT(*) AS labeled_total, COALESCE(SUM(f.is_fraud), 0) AS fraud_total "
            "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id"
        )
        labeled_total, fraud_total = result.rows[0]
        return labeled_total, fraud_total

    def _flagged(self, clause: str) -> tuple[int, int, int, int]:
        # One scan of the clause's matches. fraud_labels.transaction_id is a
        # unique key, so the LEFT JOIN never multiplies a transaction row:
        # flagged_total counts matches, flagged_labeled counts the labeled ones.
        #
        # RULE_EVAL_FROM is the exact FROM shape `validate_clause` compiles the
        # clause in, so column resolution — including correlated subqueries —
        # behaves identically here and at validation: a clause the draft/save
        # gate accepts never fails the backtest.
        result = self._executor.execute(
            "SELECT COUNT(*) AS flagged_total, "
            "COUNT(f.transaction_id) AS flagged_labeled, "
            "COALESCE(SUM(f.is_fraud), 0) AS fraud_caught, "
            "COALESCE(SUM(CASE WHEN f.is_fraud = 0 THEN 1 ELSE 0 END), 0) AS legit_blocked "
            f"{RULE_EVAL_FROM} "
            f"WHERE ({clause})"
        )
        flagged_total, flagged_labeled, fraud_caught, legit_blocked = result.rows[0]
        return flagged_total, flagged_labeled, fraud_caught, legit_blocked
