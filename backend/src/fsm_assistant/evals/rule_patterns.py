"""The known fraud patterns for the rule-quality eval (PRD story 30).

Each pattern replays the FSM's real path through the combined Run turn
(ADR-0006, amended by ADR-0007): a plain-English intent — the question or
hypothesis the FSM put to Explore — plus the evidence query they ran, whose
real rows ground the draft. Metric patterns pair that with the backtest
counts a faithful clause achieves on the pattern fixture database below;
structural-decline patterns give the turn no deployable segment (a
whole-table aggregate that names none, or a segment defined by the fraud
label itself) and expect a stated decline. A decline anywhere else is the
false decline ADR-0007 pins: the turn translates the segment the query
names and lets the backtest judge it, never refusing because the rows look
unremarkable. The fixture is small enough that every expectation is
verifiable arithmetic: count the rows in FIXTURE_TRANSACTIONS that the
intent describes, join FIXTURE_LABELS by eye.

Expected counts are hand-computed and hard-coded on purpose — they are the
targets the end-to-end pipeline (run → LLM draft → validation → backtest)
must hit, independent of the code under evaluation. `reference_clause` is one
faithful clause per pattern; the deterministic test suite backtests it
through the product's endpoint to prove the hand arithmetic, so fixture
drift breaks a test rather than silently invalidating the eval.

Intents pin exact thresholds and the fixture avoids boundary rows (no
transaction at exactly $500, $2, or 05:00), so equivalent clause phrasings
(`>` vs `>=`, `LIKE` vs `=`) land on identical rows. A post-cutoff Bad PIN
fraud (id 21, in Rome) is planted so a cutoff-sealing regression shows up as
a count mismatch instead of passing unnoticed.

Locations: rows 2, 6, 8, 13, 19 (and the sealed 21) sit at merchant location
2, the fixture's Rome — 5 visible labeled rows, 4 fraud, a 0.8 fraud rate
against Springfield's 3/13. The aggregate-segment pattern's evidence query
returns city names only, so a faithful clause must reach Rome through a
dimension-table subquery (the shape the prototype wrongly refused, ADR-0006).

Two honesty shapes the old drafter eval had cannot exist at the run seam and
are deliberately absent: a schema question and an inexpressible hypothesis
(the old refusal_email pattern) never produce a query to run, so there is no
run turn to score. Their decline behavior lives in the chat turn (the golden
set's unanswerable cases) and, deterministically, in the run turn's decline
tests in test_explore.py.
"""

from dataclasses import dataclass

# The pattern set is hand-computed at this cutoff: ids 1-20 are visible,
# ids 21-22 are sealed post-cutoff rows.
CUTOFF = "2019-09-01"

# id, date, card_id, amount_usd_cents, transaction_type, merchant_id,
# merchant_location_id, errors
FIXTURE_TRANSACTIONS = [
    (1, "2019-01-05 10:00:00", 1, 4500, "Chip Transaction", 1, 1, None),
    (2, "2019-01-08 02:30:00", 1, 82000, "Online Transaction", 1, 2, None),
    (3, "2019-01-12 14:00:00", 2, 12000, "Swipe Transaction", 1, 1, None),
    (4, "2019-01-20 03:15:00", 1, 150, "Online Transaction", 1, 1, "Bad CVV"),
    (5, "2019-02-02 11:00:00", 2, 6600, "Online Transaction", 1, 1, None),
    (6, "2019-02-10 01:45:00", 1, 90000, "Online Transaction", 1, 2, "Bad PIN"),
    (7, "2019-02-14 16:30:00", 2, 3000, "Chip Transaction", 1, 1, "Bad PIN"),
    (8, "2019-02-21 04:00:00", 2, 55000, "Online Transaction", 1, 2, None),
    (9, "2019-03-01 14:10:00", 1, 175, "Online Transaction", 1, 1, None),
    (10, "2019-03-05 13:00:00", 2, 8000, "Swipe Transaction", 1, 1, "Insufficient Balance"),
    (11, "2019-03-11 02:30:00", 1, 60000, "Online Transaction", 1, 1, None),  # unlabeled
    (12, "2019-03-18 03:30:00", 2, 2500, "Chip Transaction", 1, 1, None),
    (13, "2019-04-02 03:50:00", 1, 70000, "Online Transaction", 1, 2, "Bad PIN"),
    (14, "2019-04-09 15:00:00", 2, 150, "Swipe Transaction", 1, 1, None),
    (15, "2019-04-15 12:00:00", 1, 20000, "Chip Transaction", 1, 1, "Technical Glitch"),  # unlabeled
    (16, "2019-05-01 02:00:00", 1, 120, "Online Transaction", 1, 1, None),
    (17, "2019-05-06 18:00:00", 2, 62000, "Online Transaction", 1, 1, None),
    (18, "2019-05-12 08:00:00", 1, 100000, "Chip Transaction", 1, 1, None),
    (19, "2019-06-03 01:20:00", 1, 65000, "Online Transaction", 1, 2, None),
    (20, "2019-06-10 12:00:00", 2, 5000, "Swipe Transaction", 1, 1, "Bad PIN"),
    (21, "2019-09-15 02:00:00", 1, 88000, "Online Transaction", 1, 2, "Bad PIN"),  # post-cutoff
    (22, "2019-10-01 12:00:00", 2, 4000, "Chip Transaction", 1, 1, None),  # post-cutoff
]

# Everything labeled except 11 and 15; fraud: 2, 4, 6, 9, 13, 16, 19 (+ the
# sealed 21). The provided dataset stores label transaction_ids as TEXT.
FIXTURE_LABELS = [
    (str(i), 1 if i in {2, 4, 6, 9, 13, 16, 19, 21} else 0)
    for i in range(1, 23)
    if i not in {11, 15}
]

# The visible labeled slice: 18 labeled rows, 7 of them fraud.
LABELED_TOTAL = 18
FRAUD_TOTAL = 7

# Fraud rate over labeled transactions only, the way the Explore prompt
# teaches — the evidence queries below all follow this shape.
_LABELED_JOIN = "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id"


@dataclass(frozen=True)
class ExpectedCounts:
    """The backtest counts a faithful clause achieves. Integer counts, not
    ratios: precision/recall/score follow from them deterministically, and
    integers compare exactly."""

    flagged_total: int
    flagged_labeled: int
    fraud_caught: int
    legit_blocked: int


@dataclass(frozen=True)
class RulePattern:
    id: str
    # The FSM's message in the Explore chat, replayed to the combined turn.
    intent: str
    # The evidence query the FSM ran; its real rows ground the draft.
    sql: str
    # One faithful clause, used by the deterministic tests to prove the
    # hand-computed counts. None for structural-decline patterns, where the
    # run names no deployable segment and the expected turn behavior is a
    # stated decline.
    reference_clause: str | None = None
    expected: ExpectedCounts | None = None

    @property
    def expects_decline(self) -> bool:
        return self.expected is None


RULE_PATTERNS: list[RulePattern] = [
    RulePattern(
        id="bad_pin",
        intent="Flag every transaction that failed with a Bad PIN error.",
        sql=(
            "SELECT COUNT(*) AS bad_pin_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE t.errors = 'Bad PIN'"
        ),
        reference_clause="errors = 'Bad PIN'",
        # Visible Bad PIN rows: 6, 7, 13, 20 (21 is sealed). Fraud: 6, 13.
        expected=ExpectedCounts(
            flagged_total=4, flagged_labeled=4, fraud_caught=2, legit_blocked=2
        ),
    ),
    RulePattern(
        id="large_online",
        intent="Flag online transactions of more than $500.",
        sql=(
            "SELECT COUNT(*) AS large_online_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE t.transaction_type = 'Online Transaction' "
            "AND t.amount_usd_cents > 50000"
        ),
        reference_clause=(
            "transaction_type = 'Online Transaction' AND amount_usd_cents > 50000"
        ),
        # Rows 2, 6, 8, 11, 13, 17, 19; 11 is unlabeled. Fraud: 2, 6, 13, 19.
        expected=ExpectedCounts(
            flagged_total=7, flagged_labeled=6, fraud_caught=4, legit_blocked=2
        ),
    ),
    RulePattern(
        id="early_hours",
        intent=(
            "Flag transactions made in the early hours of the morning: from "
            "midnight up to, but not including, 5 AM."
        ),
        sql=(
            "SELECT COUNT(*) AS early_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE strftime('%H', t.date) < '05'"
        ),
        reference_clause="strftime('%H', date) < '05'",
        # Rows 2, 4, 6, 8, 11, 12, 13, 16, 19; 11 is unlabeled.
        # Fraud: 2, 4, 6, 13, 16, 19.
        expected=ExpectedCounts(
            flagged_total=9, flagged_labeled=8, fraud_caught=6, legit_blocked=2
        ),
    ),
    RulePattern(
        id="micro_amount",
        intent="Flag transactions of less than $2.",
        sql=(
            "SELECT COUNT(*) AS micro_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE t.amount_usd_cents < 200"
        ),
        reference_clause="amount_usd_cents < 200",
        # Rows 4, 9, 14, 16. Fraud: 4, 9, 16.
        expected=ExpectedCounts(
            flagged_total=4, flagged_labeled=4, fraud_caught=3, legit_blocked=1
        ),
    ),
    RulePattern(
        id="any_error",
        intent="Flag every transaction that had a processing error of any kind.",
        sql=(
            "SELECT COUNT(*) AS error_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE t.errors IS NOT NULL"
        ),
        reference_clause="errors IS NOT NULL",
        # Rows 4, 6, 7, 10, 13, 15, 20; 15 is unlabeled. Fraud: 4, 6, 13.
        expected=ExpectedCounts(
            flagged_total=7, flagged_labeled=6, fraud_caught=3, legit_blocked=3
        ),
    ),
    RulePattern(
        id="large_online_early",
        intent=(
            "Flag online transactions of more than $500 made in the early hours "
            "of the morning: from midnight up to, but not including, 5 AM."
        ),
        sql=(
            "SELECT COUNT(*) AS large_early_labeled, SUM(f.is_fraud) AS fraud "
            f"{_LABELED_JOIN} WHERE t.transaction_type = 'Online Transaction' "
            "AND t.amount_usd_cents > 50000 AND strftime('%H', t.date) < '05'"
        ),
        reference_clause=(
            "transaction_type = 'Online Transaction' AND amount_usd_cents > 50000 "
            "AND strftime('%H', date) < '05'"
        ),
        # large_online minus the evening row 17: rows 2, 6, 8, 11, 13, 19;
        # 11 is unlabeled. Fraud: 2, 6, 13, 19.
        expected=ExpectedCounts(
            flagged_total=6, flagged_labeled=5, fraud_caught=4, legit_blocked=1
        ),
    ),
    RulePattern(
        id="aggregate_segment",
        intent=(
            "Which city has the highest fraud rate? If one clearly stands out, "
            "I want to flag transactions there."
        ),
        # City names only, no location ids: a faithful clause must reach the
        # segment through a dimension-table subquery.
        sql=(
            "SELECT ml.city, COUNT(*) AS labeled, SUM(f.is_fraud) AS fraud, "
            "ROUND(1.0 * SUM(f.is_fraud) / COUNT(*), 3) AS fraud_rate "
            f"{_LABELED_JOIN} "
            "JOIN merchant_locations ml ON ml.id = t.merchant_location_id "
            "GROUP BY ml.city ORDER BY fraud_rate DESC"
        ),
        reference_clause=(
            "merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')"
        ),
        # Visible Rome rows: 2, 6, 8, 13, 19 (21 is sealed). Fraud: 2, 6, 13, 19.
        expected=ExpectedCounts(
            flagged_total=5, flagged_labeled=5, fraud_caught=4, legit_blocked=1
        ),
    ),
    RulePattern(
        id="segment_listing",
        # ADR-0007's repro: a plain listing of a named segment. The rows carry
        # no fraud column, so the old contract refused to draft; the turn must
        # translate the segment anyway and let the backtest judge it.
        intent="List the transactions at merchants in Rome.",
        sql=(
            "SELECT t.id, t.date, t.amount_usd_cents, t.transaction_type, t.errors "
            "FROM transactions t "
            "JOIN merchant_locations ml ON ml.id = t.merchant_location_id "
            "WHERE ml.city = 'Rome' ORDER BY t.date"
        ),
        reference_clause=(
            "merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')"
        ),
        # Visible Rome rows: 2, 6, 8, 13, 19 (21 is sealed). Fraud: 2, 6, 13, 19.
        expected=ExpectedCounts(
            flagged_total=5, flagged_labeled=5, fraud_caught=4, legit_blocked=1
        ),
    ),
    # Whole-table aggregates: the query names no segment, so there is
    # nothing to filter on — the first structural decline.
    RulePattern(
        id="whole_table_count",
        intent="How many transactions are there in total?",
        sql="SELECT COUNT(*) AS transaction_count FROM transactions",
    ),
    RulePattern(
        id="whole_table_distribution",
        intent="What's the mix of transaction types?",
        sql=(
            "SELECT transaction_type, COUNT(*) AS n FROM transactions "
            "GROUP BY transaction_type ORDER BY n DESC"
        ),
    ),
    # Label-defined segments: the query's filter IS the fraud label, which a
    # deployed rule can never reference, so nothing deployable remains.
    RulePattern(
        id="label_scoped_listing",
        intent="Show me the fraudulent transactions.",
        sql=(
            "SELECT t.id, t.date, t.transaction_type "
            f"{_LABELED_JOIN} WHERE f.is_fraud = 1 ORDER BY t.date"
        ),
    ),
    RulePattern(
        id="label_mixed_predicates",
        # Mixing the label with a deployable predicate must decline as a
        # whole: dropping `is_fraud = 1` would silently broaden "fraudulent
        # high-value transactions" to all high-value transactions.
        intent="Show me the fraudulent transactions over $500.",
        sql=(
            "SELECT t.id, t.date, t.amount_usd_cents, t.transaction_type "
            f"{_LABELED_JOIN} WHERE f.is_fraud = 1 AND t.amount_usd_cents > 50000 "
            "ORDER BY t.date"
        ),
    ),
]
