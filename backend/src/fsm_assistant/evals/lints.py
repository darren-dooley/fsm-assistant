"""SQL shape lints for the golden-set eval (issue #14).

Result matching alone can pass or fail for the wrong reason. A diluted
fraud-rate denominator fails only as an opaque "result mismatch", hiding the
defect the eval exists to pin: `LEFT JOIN fraud_labels` with unlabeled rows
coalesced to non-fraud. And a needless `fraud_labels` reference on an
attribute-only question can still return matching numbers, so result matching
alone would score a query the FSM cannot turn into a rule as a pass.

Each lint compares the predicted SQL against the shape the golden SQL shows
the question needs. The golden SQL referencing `fraud_labels` marks a fraud
question; absence marks an attribute-only question. Lints are cheap regex
checks on surface form, deliberately narrow: they name the two known-bad
shapes and the one known-bad table reference, nothing speculative.
"""

import re

_LEFT_JOIN_LABELS = re.compile(r"\bleft\s+(?:outer\s+)?join\s+fraud_labels\b", re.IGNORECASE)
_COALESCED_LABEL = re.compile(
    r"\b(?:coalesce|ifnull)\s*\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?is_fraud\b",
    re.IGNORECASE,
)
_LABEL_REFERENCE = re.compile(r"\bfraud_labels\b|\bis_fraud\b", re.IGNORECASE)


def lint_sql(golden_sql: str, predicted_sql: str) -> list[str]:
    """Shape defects in `predicted_sql`, given the golden SQL for the case.

    Returns lint identifiers; empty means clean. On a fraud question,
    `left_join_fraud_labels` and `coalesced_is_fraud` name the diluted
    denominator (labels cover about two thirds of transactions, so treating
    unlabeled rows as non-fraud understates every rate). On an attribute-only
    question, `needless_fraud_labels` names any label reference at all: an
    inner join silently drops the unlabeled third, and a label-joined query is
    one the FSM cannot translate into a deployable rule.
    """
    lints: list[str] = []
    if _LABEL_REFERENCE.search(golden_sql):
        if _LEFT_JOIN_LABELS.search(predicted_sql):
            lints.append("left_join_fraud_labels")
        if _COALESCED_LABEL.search(predicted_sql):
            lints.append("coalesced_is_fraud")
    elif _LABEL_REFERENCE.search(predicted_sql):
        lints.append("needless_fraud_labels")
    return lints


def is_single_line(sql: str) -> bool:
    """True when the SQL is one long line, which the Explore UI renders with
    heavy horizontal scrolling. Tracked as a rate, not a correctness failure."""
    return "\n" not in sql.strip()
