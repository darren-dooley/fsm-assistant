"""The rule-quality pattern set pairs each intent with hand-computed expected
backtest counts on the pattern fixture database (PRD story 30). These tests
re-derive every expectation through the product's backtest endpoint, so the
hand arithmetic in rule_patterns.py is verified deterministically — including
that the post-cutoff plants stay sealed (a post-cutoff Bad PIN fraud exists
precisely so a sealing regression would surface as a count mismatch).
"""

import pytest

from fsm_assistant.evals.rule_patterns import (
    CUTOFF,
    FIXTURE_LABELS,
    FIXTURE_TRANSACTIONS,
    FRAUD_TOTAL,
    LABELED_TOTAL,
    RULE_PATTERNS,
)

_METRIC_PATTERNS = [p for p in RULE_PATTERNS if not p.expects_decline]


def test_pattern_cutoff_matches_test_wiring():
    # make_client builds its app at the conftest cutoff; the pattern set is
    # hand-computed at the same one.
    from conftest import CUTOFF as TEST_CUTOFF

    assert CUTOFF == TEST_CUTOFF


def test_set_covers_both_expectations():
    assert len(_METRIC_PATTERNS) >= 5
    # Structural declines stay measured: a whole-table aggregate names no
    # segment, so those runs must not force rules.
    assert len([p for p in RULE_PATTERNS if p.expects_decline]) >= 2


def test_set_pins_the_plain_segment_listing_regression():
    # ADR-0007's repro: a listing query whose rows carry no fraud column must
    # still draft — the turn translates the segment, the backtest judges it.
    # At least one metric pattern's evidence query never touches the labels.
    label_free = [
        p
        for p in _METRIC_PATTERNS
        if "fraud_labels" not in p.sql and "is_fraud" not in p.sql
    ]
    assert label_free, "no metric pattern replays a label-free segment listing"


def test_set_pins_label_defined_segments_as_declines():
    # A segment defined by the fraud label itself can't deploy (ADR-0007's
    # second structural reason). Both shapes must expect a decline: the pure
    # label filter, and the label mixed with a deployable predicate — which
    # declines as a whole rather than being silently stripped and broadened.
    decline_sql = [p.sql for p in RULE_PATTERNS if p.expects_decline]
    assert any(
        "is_fraud = 1" in sql and "amount_usd_cents" not in sql for sql in decline_sql
    ), "no pure label-filter decline pattern"
    assert any(
        "is_fraud = 1" in sql and "amount_usd_cents" in sql for sql in decline_sql
    ), "no label-mixed-with-predicate decline pattern"


def test_every_evidence_query_runs_clean(make_client):
    # Each pattern's evidence query is part of the eval's fixed input: it must
    # execute under the product guardrails, or the eval scores a broken run.
    client, _ = make_client(
        # Scripted decline replies: this test only exercises the SQL.
        ['{"summary": "s", "decline": "n"}'] * len(RULE_PATTERNS),
        transactions=FIXTURE_TRANSACTIONS,
        labels=FIXTURE_LABELS,
    )
    for pattern in RULE_PATTERNS:
        body = client.post(
            "/api/explore/run",
            json={"sql": pattern.sql, "history": [{"role": "user", "content": pattern.intent}]},
        ).json()
        assert body["status"] == "ok", f"{pattern.id}: {body.get('message')}"
        assert body["rows"], pattern.id


@pytest.mark.parametrize("pattern", _METRIC_PATTERNS, ids=lambda p: p.id)
def test_reference_clause_reproduces_hand_computed_counts(make_client, pattern):
    client, _ = make_client(
        [], transactions=FIXTURE_TRANSACTIONS, labels=FIXTURE_LABELS
    )
    body = client.post(
        "/api/rules/backtest", json={"clause": pattern.reference_clause}
    ).json()
    assert body["status"] == "ok"
    backtest = body["backtest"]
    expected = pattern.expected
    assert backtest["flagged_total"] == expected.flagged_total
    assert backtest["flagged_labeled"] == expected.flagged_labeled
    assert backtest["fraud_caught"] == expected.fraud_caught
    assert backtest["legit_blocked"] == expected.legit_blocked
    assert backtest["labeled_total"] == LABELED_TOTAL
    assert backtest["fraud_total"] == FRAUD_TOTAL
