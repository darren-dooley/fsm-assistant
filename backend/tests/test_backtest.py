"""Backtest metrics at the HTTP seam, asserted against a hand-computable
fixture so precision, recall, lift, and the Score are verifiable arithmetic
rather than snapshots (PRD stories 15-19, 35, 41).

The pre-cutoff labeled slice below holds 7 labeled transactions, 3 of them
fraud (base rate 3/7). The clause `amount_usd_cents >= 50000` matches five
transactions: three fraud, one legit, and one unlabeled (excluded). A post-
cutoff fraud (id 10) must stay sealed, so fraud_total is 3, not 4.
"""

# id, date, card_id, amount_usd_cents, transaction_type, merchant_id, location, errors
BACKTEST_TRANSACTIONS = [
    (1, "2019-01-01 10:00:00", 1, 60000, "Online Transaction", 1, 1, None),  # fraud, matches
    (2, "2019-01-02 10:00:00", 1, 70000, "Online Transaction", 1, 1, None),  # fraud, matches
    (3, "2019-01-03 10:00:00", 1, 80000, "Online Transaction", 1, 1, None),  # fraud, matches
    (4, "2019-01-04 10:00:00", 1, 55000, "Online Transaction", 1, 1, None),  # legit, matches (FP)
    (5, "2019-01-05 10:00:00", 1, 10000, "Swipe Transaction", 1, 1, None),  # legit, no match
    (6, "2019-01-06 10:00:00", 1, 20000, "Swipe Transaction", 1, 1, None),  # legit, no match
    (7, "2019-01-07 10:00:00", 1, 30000, "Swipe Transaction", 1, 1, None),  # legit, no match
    (8, "2019-01-08 10:00:00", 1, 90000, "Online Transaction", 1, 1, None),  # unlabeled, matches
    (9, "2019-01-09 10:00:00", 1, 5000, "Swipe Transaction", 1, 1, None),  # unlabeled, no match
    (10, "2019-10-01 10:00:00", 1, 99999, "Online Transaction", 1, 1, None),  # post-cutoff fraud
]

BACKTEST_LABELS = [
    ("1", 1), ("2", 1), ("3", 1), ("4", 0), ("5", 0), ("6", 0), ("7", 0), ("10", 1)
]


def backtest_client(make_client):
    client, _ = make_client([], transactions=BACKTEST_TRANSACTIONS, labels=BACKTEST_LABELS)
    return client


def test_backtest_metrics_are_verifiable_arithmetic(make_client):
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "amount_usd_cents >= 50000"}).json()
    assert body["status"] == "ok"
    b = body["backtest"]

    # Raw counts, always beside the ratios.
    assert b["flagged_total"] == 5
    assert b["flagged_labeled"] == 4
    assert b["flagged_unlabeled"] == 1  # the excluded, unlabeled match, reported
    assert b["fraud_caught"] == 3
    assert b["legit_blocked"] == 1
    assert b["labeled_total"] == 7
    assert b["fraud_total"] == 3  # the post-cutoff fraud (id 10) stays sealed

    # Ratios: precision 3/4, recall 3/3, base 3/7, lift precision/base.
    assert b["precision"] == 0.75
    assert b["recall"] == 1.0
    assert abs(b["base_rate"] - 3 / 7) < 1e-9
    assert abs(b["lift"] - (0.75 / (3 / 7))) < 1e-9  # == 1.75
    assert abs(b["legit_blocked_per_fraud_caught"] - 1 / 3) < 1e-9

    # Score = 100 * (0.4*0.75 + 0.4*1.0 + 0.2*min(1.75/10, 1)) = 73.5, never alone.
    assert b["score"] == 73.5
    assert b["evidence_basis"] == "in-sample (pre-cutoff)"


def test_backtest_reports_share_of_known_fraud(make_client):
    # A clause catching one of three known fraud: recall (share of known fraud) 1/3.
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "amount_usd_cents = 60000"}).json()
    b = body["backtest"]
    assert b["fraud_caught"] == 1
    assert abs(b["recall"] - 1 / 3) < 1e-9
    assert b["precision"] == 1.0
    assert b["legit_blocked"] == 0
    assert b["legit_blocked_per_fraud_caught"] == 0.0  # blocks zero legit per fraud caught


def test_backtest_of_clause_matching_nothing_scores_zero(make_client):
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "amount_usd_cents > 1000000"}).json()
    b = body["backtest"]
    assert b["flagged_total"] == 0
    assert b["fraud_caught"] == 0
    assert b["precision"] == 0.0
    assert b["recall"] == 0.0
    assert b["lift"] == 0.0
    assert b["score"] == 0.0


def test_backtest_excludes_post_cutoff_fraud(make_client):
    # A clause that would catch the sealed post-cutoff fraud (id 10) if the view
    # leaked it. It must not: no pre-cutoff transaction has this amount.
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "amount_usd_cents = 99999"}).json()
    b = body["backtest"]
    assert b["flagged_total"] == 0
    assert b["fraud_caught"] == 0


def test_malformed_clause_is_rejected_not_executed(make_client):
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "amount_usd_cents >>> 5"}).json()
    assert body["status"] == "invalid"
    assert "backtest" not in body


def test_unknown_column_clause_is_rejected(make_client):
    client = backtest_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": "made_up_column = 1"}).json()
    assert body["status"] == "invalid"


def test_unsafe_clause_cannot_reach_sealed_table(make_client):
    # A subquery reaching the raw post-cutoff table must be denied by the
    # authorizer, exactly as in exploration.
    client = backtest_client(make_client)
    body = client.post(
        "/api/rules/backtest",
        json={"clause": "id IN (SELECT id FROM main.transactions)"},
    ).json()
    assert body["status"] == "invalid"
