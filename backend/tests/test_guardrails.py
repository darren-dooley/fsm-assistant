"""Guardrail behavior: hostile SQL must yield a safe error and never execute,
whether the model proposes it in chat (validation rejects it there) or it
reaches the run endpoint directly; the row cap, timeout, and pre-cutoff view
are enforced by the application, not the model."""

import sqlite3

from conftest import chat_reply, decline_reply


def _assert_data_db_intact(tmp_path):
    conn = sqlite3.connect(tmp_path / "data.db")
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
    conn.close()


def _run(client, sql: str) -> dict:
    return client.post("/api/explore/run", json={"sql": sql}).json()


def test_chat_never_proposes_non_select_sql(make_client, tmp_path):
    # The model insists on a DELETE; validation rejects every attempt and the
    # bounded loop ends in an error with no SQL proposed and nothing executed.
    client, _ = make_client([chat_reply("Deleting.", "DELETE FROM fraud_labels")] * 3)
    body = client.post("/api/explore", json={"message": "Delete everything"}).json()
    assert body["status"] == "error"
    assert body["sql"] is None
    _assert_data_db_intact(tmp_path)


def test_run_rejects_non_select_sql(make_client, tmp_path):
    client, _ = make_client([])
    body = _run(client, "DELETE FROM fraud_labels")
    assert body["status"] == "error"
    assert body["rows"] == []
    _assert_data_db_intact(tmp_path)


def test_run_rejects_multi_statement_sql(make_client, tmp_path):
    client, _ = make_client([])
    assert _run(client, "SELECT 1; DROP TABLE transactions")["status"] == "error"
    _assert_data_db_intact(tmp_path)


def test_run_rejects_write_shaped_with_clause(make_client, tmp_path):
    client, _ = make_client([])
    body = _run(client, "WITH x AS (SELECT 1) INSERT INTO fraud_labels VALUES ('9', 1)")
    assert body["status"] == "error"
    _assert_data_db_intact(tmp_path)


def test_run_rejects_pragma(make_client):
    client, _ = make_client([])
    assert _run(client, "PRAGMA writable_schema = 1")["status"] == "error"


def test_row_limit_truncates_results(make_client):
    client, _ = make_client([decline_reply("Here are the ids.", "Just ids.")], row_limit=2)
    body = _run(client, "SELECT id FROM transactions ORDER BY id")
    assert body["status"] == "ok"
    assert body["rows"] == [[1], [2]]
    assert body["truncated"] is True


def test_runaway_query_is_cut_off_with_clear_message(make_client):
    runaway = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
        "SELECT COUNT(*) FROM c"
    )
    client, fake = make_client([], query_timeout_ms=100)
    body = _run(client, runaway)
    assert body["status"] == "error"
    assert "cut off" in body["message"]
    # A timed-out run produces no rows to summarize; the LLM is never called.
    assert len(fake.calls) == 0


def test_execution_sees_only_pre_cutoff_data(make_client):
    client, _ = make_client([decline_reply("Counts computed.", "Just counts.")])
    body = _run(
        client,
        "SELECT (SELECT COUNT(*) FROM transactions) AS tx, "
        "(SELECT COUNT(*) FROM fraud_labels) AS labels, "
        "(SELECT SUM(is_fraud) FROM fraud_labels) AS fraud",
    )
    # Transaction 5 (2019-09-15, labeled fraud) sits past the cutoff: the
    # pre-T view exposes 4 of 5 transactions and 3 of 4 labels.
    assert body["rows"] == [[4, 3, 1]]


def test_schema_qualified_names_cannot_bypass_the_cutoff(make_client):
    # `main.transactions` / `main.fraud_labels` name the raw tables
    # underneath the pre-T views; the authorizer must reject them at chat
    # validation and at run, or the seal is decorative.
    client, _ = make_client(
        [chat_reply("Counting.", "SELECT COUNT(*) FROM main.transactions")] * 3
    )
    chat_body = client.post("/api/explore", json={"message": "How many really?"}).json()
    assert chat_body["status"] == "error"
    for sql in ("SELECT COUNT(*) FROM main.transactions", "SELECT COUNT(*) FROM main.fraud_labels"):
        body = _run(client, sql)
        assert body["status"] == "error"
        assert body["rows"] == []


def test_join_uses_pre_cutoff_labels_only(make_client):
    client, _ = make_client([decline_reply("Three labeled transactions.", "Just a listing.")])
    body = _run(
        client,
        "SELECT t.id, f.is_fraud FROM transactions t "
        "JOIN fraud_labels f ON f.transaction_id = t.id ORDER BY t.id",
    )
    assert body["rows"] == [[1, 0], [2, 1], [3, 0]]
