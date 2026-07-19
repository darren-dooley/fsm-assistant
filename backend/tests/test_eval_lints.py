"""Shape lints name the SQL defects result matching cannot (issue #14): the
diluted fraud-rate denominator, and fraud_labels dragged into attribute-only
questions where any join drops the unlabeled third."""

from fsm_assistant.evals.lints import is_single_line, lint_sql

FRAUD_GOLDEN = (
    "SELECT ROUND(100.0 * SUM(is_fraud) / COUNT(*), 3) AS fraud_rate_pct "
    "FROM fraud_labels"
)
ATTRIBUTE_GOLDEN = "SELECT COUNT(*) AS n FROM transactions"


def test_left_join_on_fraud_question_is_flagged():
    predicted = (
        "SELECT ROUND(100.0 * SUM(f.is_fraud) / COUNT(f.transaction_id), 3) AS r\n"
        "FROM transactions t\n"
        "LEFT JOIN fraud_labels f ON f.transaction_id = t.id"
    )
    assert "left_join_fraud_labels" in lint_sql(FRAUD_GOLDEN, predicted)


def test_left_outer_join_is_flagged_too():
    predicted = "SELECT 1 FROM transactions t LEFT OUTER JOIN fraud_labels f ON f.transaction_id = t.id"
    assert "left_join_fraud_labels" in lint_sql(FRAUD_GOLDEN, predicted)


def test_coalesced_label_is_flagged():
    predicted = (
        "SELECT ROUND(100.0 * SUM(COALESCE(f.is_fraud, 0)) / COUNT(*), 3) AS r "
        "FROM transactions t LEFT JOIN fraud_labels f ON f.transaction_id = t.id"
    )
    lints = lint_sql(FRAUD_GOLDEN, predicted)
    assert "coalesced_is_fraud" in lints
    assert "left_join_fraud_labels" in lints


def test_ifnull_counts_as_coalesced():
    predicted = "SELECT SUM(IFNULL(is_fraud, 0)) AS n FROM fraud_labels"
    assert lint_sql(FRAUD_GOLDEN, predicted) == ["coalesced_is_fraud"]


def test_inner_join_with_labeled_denominator_is_clean():
    predicted = (
        "SELECT ROUND(100.0 * SUM(f.is_fraud) / COUNT(*), 3) AS r\n"
        "FROM transactions t\n"
        "JOIN fraud_labels f ON f.transaction_id = t.id"
    )
    assert lint_sql(FRAUD_GOLDEN, predicted) == []


def test_label_reference_on_attribute_question_is_flagged():
    joined = (
        "SELECT COUNT(*) AS n FROM transactions t "
        "JOIN fraud_labels f ON f.transaction_id = t.id"
    )
    assert lint_sql(ATTRIBUTE_GOLDEN, joined) == ["needless_fraud_labels"]
    column_only = "SELECT id, is_fraud AS context FROM transactions"
    assert lint_sql(ATTRIBUTE_GOLDEN, column_only) == ["needless_fraud_labels"]


def test_plain_attribute_query_is_clean():
    assert lint_sql(ATTRIBUTE_GOLDEN, "SELECT COUNT(*) AS n\nFROM transactions") == []


def test_single_line_detection():
    assert is_single_line("SELECT COUNT(*) AS n FROM transactions") is True
    assert is_single_line("SELECT COUNT(*) AS n\nFROM transactions") is False
    assert is_single_line("  SELECT 1  \n") is True
