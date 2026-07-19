"""The Candidate Rule lifecycle at the HTTP seam: the save-requires-backtest
gate, the Score-ranked rule set, re-backtest, edit-reopens-Candidate, and
delete (PRD stories 14, 20-26, 39, 41). Drafting happens in the Explore run
turn (ADR-0006, covered in test_explore.py); the Workbench endpoints here are
LLM-free."""

# A small labeled slice: 4 labeled, 2 fraud. `errors = 'Bad PIN'` catches one
# fraud cleanly; `amount_usd_cents > 0` catches everything (poor precision).
TRANSACTIONS = [
    (1, "2019-01-01 10:00:00", 1, 60000, "Online Transaction", 1, 1, "Bad PIN"),  # fraud
    (2, "2019-01-02 10:00:00", 1, 70000, "Online Transaction", 1, 1, None),  # fraud
    (3, "2019-01-03 10:00:00", 1, 10000, "Swipe Transaction", 1, 1, None),  # legit
    (4, "2019-01-04 10:00:00", 1, 20000, "Swipe Transaction", 1, 1, None),  # legit
]
LABELS = [("1", 1), ("2", 1), ("3", 0), ("4", 0)]


def rules_client(make_client, responses=None):
    return make_client(responses or [], transactions=TRANSACTIONS, labels=LABELS)


def test_draft_endpoint_is_gone(make_client):
    # ADR-0006 deleted the separate Drafter: drafting lives in the Explore
    # run turn, and the Workbench performs no LLM-backed requests.
    client, _ = rules_client(make_client)
    response = client.post(
        "/api/rules/draft",
        json={"messages": [{"role": "user", "content": "flag bad PIN declines"}]},
    )
    # 405, not 404: the path now only matches PUT /api/rules/{rule_id}. Either
    # way there is no POST route left to draft against.
    assert response.status_code in (404, 405)
    assert "clause" not in response.json()


def test_save_persists_clause_name_description_and_snapshot(make_client):
    client, _ = rules_client(make_client)
    save = client.post(
        "/api/rules",
        json={
            "name": "Bad PIN declines",
            "description": "Online declines for a bad PIN.",
            "clause": "errors = 'Bad PIN'",
        },
    ).json()
    assert save["status"] == "ok"
    rule = save["rule"]
    assert rule["name"] == "Bad PIN declines"
    assert rule["description"] == "Online declines for a bad PIN."
    assert rule["clause"] == "errors = 'Bad PIN'"
    # The snapshot is the Backtest of the exact saved clause: 1 fraud caught,
    # 0 legit blocked, precision 1.0.
    assert rule["backtest"]["fraud_caught"] == 1
    assert rule["backtest"]["legit_blocked"] == 0
    assert rule["backtest"]["precision"] == 1.0
    assert rule["score"] == rule["backtest"]["score"]

    listed = client.get("/api/rules").json()["rules"]
    assert [r["id"] for r in listed] == [rule["id"]]


def test_table_qualified_clause_validates_and_backtests_consistently(make_client):
    # A table-qualified column like `transactions.errors` must compile the same
    # way in validation and in the backtest, or the save/backtest gate rejects a
    # clause validation just accepted (issue #12, problem 3). The backtest query
    # once aliased `transactions AS t`, hiding the table name and erroring on
    # `no such column: transactions.errors`.
    client, _ = rules_client(make_client)
    qualified = client.post(
        "/api/rules/backtest", json={"clause": "transactions.errors IS NOT NULL"}
    ).json()
    assert qualified["status"] == "ok"

    # It denotes the same slice as the unqualified form: id 1 (Bad PIN, fraud).
    plain = client.post(
        "/api/rules/backtest", json={"clause": "errors IS NOT NULL"}
    ).json()
    assert qualified["backtest"] == plain["backtest"]
    assert qualified["backtest"]["flagged_total"] == 1
    assert qualified["backtest"]["fraud_caught"] == 1

    # The save gate re-backtests the exact clause; a table-qualified one saves.
    save = client.post(
        "/api/rules",
        json={"name": "Any error", "clause": "transactions.errors IS NOT NULL"},
    ).json()
    assert save["status"] == "ok"
    assert save["rule"]["backtest"]["fraud_caught"] == 1


ORIGINAL_LABEL_LEAK = (
    "EXISTS (SELECT 1 FROM fraud_labels f2 JOIN transactions t2 "
    "ON t2.id = f2.transaction_id "
    "WHERE t2.transaction_type = transactions.transaction_type "
    "AND t2.merchant_location_id = transactions.merchant_location_id "
    "GROUP BY t2.transaction_type, t2.merchant_location_id "
    "HAVING SUM(CASE WHEN f2.is_fraud = 1 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) >= 0.8)"
)


def test_label_reading_clause_is_rejected_with_teaching_message(make_client):
    # The clause that surfaced the bug: it recomputes historical fraud rates
    # by reading fraud_labels at scoring time. Production transactions carry
    # no label yet, so the rule would be dead on deploy; it previously failed
    # with a baffling `no such column: transactions.transaction_type` from the
    # validation/backtest shape mismatch instead of being rejected for the
    # real reason.
    client, _ = rules_client(make_client)
    body = client.post("/api/rules/backtest", json={"clause": ORIGINAL_LABEL_LEAK}).json()
    assert body["status"] == "invalid"
    assert "fraud_labels" in body["message"]
    assert "unlabeled" in body["message"]


def test_label_reads_are_denied_through_every_path(make_client):
    client, _ = rules_client(make_client)
    for clause in (
        "transactions.id IN (SELECT transaction_id FROM fraud_labels WHERE is_fraud = 1)",
        "(SELECT COUNT(*) FROM fraud_labels) > 0",
        # The backtest query's own join alias must not leak into clauses.
        "f.is_fraud = 1",
    ):
        body = client.post("/api/rules/backtest", json={"clause": clause}).json()
        assert body["status"] == "invalid", clause


def test_dimension_subquery_clause_is_deployable(make_client):
    # The assignment's own example shape: a subquery over a dimension table
    # stays allowed — only the label table is off limits.
    client, _ = rules_client(make_client)
    body = client.post(
        "/api/rules/backtest",
        json={"clause": "card_id IN (SELECT id FROM cards WHERE card_type = 'Debit')"},
    ).json()
    assert body["status"] == "ok"


def test_correlated_grouped_clause_validates_and_backtests_consistently(make_client):
    # The subquery pattern that exposed the shape mismatch (passed the old
    # simple validation shape, failed the joined backtest shape), rebuilt
    # without labels. Validation now compiles the exact backtest statement
    # shape, so accept-then-fail is impossible.
    client, _ = rules_client(make_client)
    clause = (
        "EXISTS (SELECT 1 FROM transactions t2 "
        "WHERE t2.card_id = transactions.card_id "
        "GROUP BY t2.card_id HAVING COUNT(*) >= 2)"
    )
    body = client.post("/api/rules/backtest", json={"clause": clause}).json()
    assert body["status"] == "ok"
    # Every fixture transaction shares card 1, so the clause flags all four.
    assert body["backtest"]["flagged_total"] == 4


def test_invalid_clause_cannot_be_saved(make_client):
    client, _ = rules_client(make_client)
    body = client.post(
        "/api/rules", json={"name": "bad", "clause": "not_a_column = 1"}
    ).json()
    assert body["status"] == "invalid"
    assert client.get("/api/rules").json()["rules"] == []


def test_rule_set_is_ranked_by_score(make_client):
    client, _ = rules_client(make_client)
    # A precise rule (1 fraud, 0 legit) outscores a blunt one (all four rows).
    client.post("/api/rules", json={"name": "precise", "clause": "errors = 'Bad PIN'"})
    client.post("/api/rules", json={"name": "blunt", "clause": "amount_usd_cents > 0"})
    listed = client.get("/api/rules").json()["rules"]
    assert [r["name"] for r in listed] == ["precise", "blunt"]
    assert listed[0]["score"] > listed[1]["score"]


def test_saved_rule_can_be_rebacktested_on_demand(make_client):
    client, _ = rules_client(make_client)
    rule = client.post(
        "/api/rules", json={"name": "precise", "clause": "errors = 'Bad PIN'"}
    ).json()["rule"]
    again = client.post(f"/api/rules/{rule['id']}/backtest").json()
    assert again["status"] == "ok"
    # Deterministic: the reproduced evidence matches the saved snapshot.
    assert again["rule"]["backtest"]["fraud_caught"] == rule["backtest"]["fraud_caught"]
    assert again["rule"]["score"] == rule["score"]


def test_editing_a_rule_rebacktests_and_updates_evidence(make_client):
    client, _ = rules_client(make_client)
    rule = client.post(
        "/api/rules", json={"name": "precise", "clause": "errors = 'Bad PIN'"}
    ).json()["rule"]
    edited = client.put(
        f"/api/rules/{rule['id']}",
        json={"name": "precise v2", "clause": "amount_usd_cents > 0"},
    ).json()
    assert edited["status"] == "ok"
    assert edited["rule"]["name"] == "precise v2"
    assert edited["rule"]["clause"] == "amount_usd_cents > 0"
    # Fresh evidence for the new clause: it now blocks legitimate transactions.
    assert edited["rule"]["backtest"]["legit_blocked"] == 2
    assert edited["rule"]["backtest"]["fraud_caught"] == 2
    # Same rule id, updated in place.
    assert edited["rule"]["id"] == rule["id"]
    assert len(client.get("/api/rules").json()["rules"]) == 1


def test_editing_a_rule_to_an_invalid_clause_is_rejected(make_client):
    client, _ = rules_client(make_client)
    rule = client.post(
        "/api/rules", json={"name": "precise", "clause": "errors = 'Bad PIN'"}
    ).json()["rule"]
    body = client.put(
        f"/api/rules/{rule['id']}",
        json={"name": "precise", "clause": "nonsense_col = 1"},
    ).json()
    assert body["status"] == "invalid"
    # The original clause and evidence survive an invalid edit.
    still = client.get("/api/rules").json()["rules"][0]
    assert still["clause"] == "errors = 'Bad PIN'"


def test_rule_can_be_deleted(make_client):
    client, _ = rules_client(make_client)
    rule = client.post(
        "/api/rules", json={"name": "precise", "clause": "errors = 'Bad PIN'"}
    ).json()["rule"]
    assert client.delete(f"/api/rules/{rule['id']}").json()["status"] == "ok"
    assert client.get("/api/rules").json()["rules"] == []
    assert client.delete(f"/api/rules/{rule['id']}").status_code == 404


def test_backtest_and_operations_on_missing_rule_are_404(make_client):
    client, _ = rules_client(make_client)
    assert client.post("/api/rules/999/backtest").status_code == 404
    assert client.put(
        "/api/rules/999", json={"name": "x", "clause": "amount_usd_cents > 0"}
    ).status_code == 404
