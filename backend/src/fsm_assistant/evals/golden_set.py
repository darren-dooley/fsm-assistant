"""The golden question set (PRD story 29).

Each case is a plain-English question paired with either known-good SQL — whose
execution against the real database *is* the expected answer — or a refusal
expectation for questions the schema cannot answer. Nothing is hard-coded to a
number: the runner derives every expected result by executing the golden SQL
through the same guarded executor the product uses, so the golden set stays
correct if the dataset or cutoff changes.

The set covers the question shapes the PRD cares about: aggregate fraud-rate
questions, filters over transaction attributes, grouped breakdowns, follow-up
questions that depend on conversation context, and deliberately unanswerable
questions where the correct behavior is refusal.
"""

from dataclasses import dataclass, field
from typing import Literal

Category = Literal["aggregate", "filter", "grouped", "follow_up", "unanswerable"]


@dataclass(frozen=True)
class HistoryTurn:
    """A prior exchange replayed to give a follow-up question its context."""

    question: str
    answer: str
    sql: str | None = None


@dataclass(frozen=True)
class GoldenCase:
    id: str
    category: Category
    question: str
    # Known-good SQL whose execution result is the expected answer. None for
    # unanswerable cases, where the expected behavior is a refusal.
    sql: str | None = None
    history: list[HistoryTurn] = field(default_factory=list)

    @property
    def expects_refusal(self) -> bool:
        return self.sql is None


# Fraud rates are computed over labeled transactions only. Rate questions ask
# explicitly for a percentage and the golden SQL returns one (SUM/COUNT * 100)
# rounded to 3 decimals, matching the translator's rounding, so the golden
# value and a correct model answer round to the same number. Pinning the unit
# in the question avoids scoring a correct fraction-form answer as a miss.
GOLDEN_CASES: list[GoldenCase] = [
    # --- Aggregate fraud-rate / totals -------------------------------------
    GoldenCase(
        id="agg_overall_fraud_rate",
        category="aggregate",
        question="What is the overall fraud rate, as a percentage?",
        sql=(
            "SELECT ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM fraud_labels"
        ),
    ),
    GoldenCase(
        id="agg_total_transactions",
        category="aggregate",
        question="How many transactions are there in total?",
        sql="SELECT COUNT(*) AS n FROM transactions",
    ),
    GoldenCase(
        id="agg_fraud_count",
        category="aggregate",
        question="How many transactions are labeled as fraud?",
        sql="SELECT SUM(is_fraud) AS fraud_count FROM fraud_labels",
    ),
    GoldenCase(
        id="agg_online_share",
        category="aggregate",
        question="What percentage of transactions are online transactions?",
        sql=(
            "SELECT ROUND(100.0 * SUM(CASE WHEN transaction_type = 'Online Transaction' "
            "THEN 1 ELSE 0 END) / COUNT(*), 3) AS online_pct FROM transactions"
        ),
    ),
    # --- Filters over transaction attributes -------------------------------
    GoldenCase(
        id="filter_bad_pin_count",
        category="filter",
        question="How many transactions had a Bad PIN error?",
        sql="SELECT COUNT(*) AS n FROM transactions WHERE errors = 'Bad PIN'",
    ),
    GoldenCase(
        id="filter_online_over_500",
        category="filter",
        question="How many online transactions were for more than $500?",
        sql=(
            "SELECT COUNT(*) AS n FROM transactions "
            "WHERE transaction_type = 'Online Transaction' AND amount_usd_cents > 50000"
        ),
    ),
    GoldenCase(
        id="filter_swipe_fraud_rate",
        category="filter",
        question="What is the fraud rate for swipe transactions, as a percentage?",
        sql=(
            "SELECT ROUND(100.0 * SUM(f.is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id "
            "WHERE t.transaction_type = 'Swipe Transaction'"
        ),
    ),
    # --- Grouped breakdowns ------------------------------------------------
    GoldenCase(
        id="grouped_count_by_type",
        category="grouped",
        question="How many transactions are there of each transaction type?",
        sql=(
            "SELECT transaction_type, COUNT(*) AS n FROM transactions "
            "GROUP BY transaction_type"
        ),
    ),
    GoldenCase(
        id="grouped_fraud_rate_by_type",
        category="grouped",
        question="What is the fraud rate, as a percentage, broken down by transaction type?",
        sql=(
            "SELECT t.transaction_type, "
            "ROUND(100.0 * SUM(f.is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id "
            "GROUP BY t.transaction_type"
        ),
    ),
    # --- Follow-up questions that need conversation context ----------------
    GoldenCase(
        id="followup_chip_from_overall",
        category="follow_up",
        question="And just for chip transactions?",
        history=[
            HistoryTurn(
                question="What is the overall fraud rate, as a percentage?",
                answer="The overall fraud rate is low, computed over labeled transactions.",
                sql=(
                    "SELECT ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
                    "FROM fraud_labels"
                ),
            )
        ],
        sql=(
            "SELECT ROUND(100.0 * SUM(f.is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id "
            "WHERE t.transaction_type = 'Chip Transaction'"
        ),
    ),
    GoldenCase(
        id="followup_fraud_share_of_bad_pin",
        category="follow_up",
        question="What percentage of those are fraudulent?",
        history=[
            HistoryTurn(
                question="How many transactions had a Bad PIN error?",
                answer="A number of transactions had a Bad PIN error.",
                sql="SELECT COUNT(*) AS n FROM transactions WHERE errors = 'Bad PIN'",
            )
        ],
        sql=(
            "SELECT ROUND(100.0 * SUM(f.is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id "
            "WHERE t.errors = 'Bad PIN'"
        ),
    ),
    # --- Deliberately unanswerable: correct behavior is refusal ------------
    GoldenCase(
        id="unanswerable_email",
        category="unanswerable",
        question="Which customer email addresses are linked to fraud?",
    ),
    GoldenCase(
        id="unanswerable_ip_address",
        category="unanswerable",
        question="What is the IP address of each fraudulent transaction?",
    ),
    GoldenCase(
        id="unanswerable_write",
        category="unanswerable",
        question="Please delete every transaction that was flagged as fraud.",
    ),
]
