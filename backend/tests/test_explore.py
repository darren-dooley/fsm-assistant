"""Explore behavior at the HTTP seam: conversational replies, validated SQL
proposals that only run on request, conversation context, the bounded repair
loop, run-and-summarize, and plain-English errors."""

from conftest import chat_reply, decline_reply, rule_reply

from fsm_assistant.llm import LLMUnavailableError


def test_smalltalk_gets_a_reply_and_no_query(make_client):
    client, fake = make_client([chat_reply("Hello! Ask me about the transaction data.")])
    body = client.post("/api/explore", json={"message": "hello"}).json()
    assert body["status"] == "ok"
    assert body["reply"] == "Hello! Ask me about the transaction data."
    assert body["sql"] is None
    assert body["attempts"] == 1
    # One chat completion, nothing executed, no summarization.
    assert len(fake.calls) == 1


def test_data_question_proposes_validated_sql_without_running_it(make_client):
    client, fake = make_client(
        [chat_reply("This counts all transactions.", "SELECT COUNT(*) AS n FROM transactions")]
    )
    body = client.post("/api/explore", json={"message": "How many transactions?"}).json()
    assert body["status"] == "ok"
    assert body["reply"] == "This counts all transactions."
    assert body["sql"] == "SELECT COUNT(*) AS n FROM transactions"
    # Proposing is not executing: the response carries no rows, and no
    # summarization call was made.
    assert "rows" not in body
    assert len(fake.calls) == 1


def test_conversation_history_carries_prior_turns(make_client):
    client, fake = make_client(
        [chat_reply("Filtered to Bad PIN.", "SELECT COUNT(*) AS n FROM transactions WHERE errors = 'Bad PIN'")]
    )
    history = [
        {"role": "user", "content": "What's the overall fraud rate?"},
        {
            "role": "assistant",
            "content": "This computes the fraud rate over labeled transactions.",
            "sql": "SELECT AVG(is_fraud) FROM fraud_labels",
        },
        {"role": "assistant", "content": "About 33% of labeled transactions are fraud."},
    ]
    body = client.post(
        "/api/explore", json={"message": "And for Bad PIN errors?", "history": history}
    ).json()
    assert body["status"] == "ok"
    prompt = str(fake.calls[0])
    assert "What's the overall fraud rate?" in prompt
    assert "SELECT AVG(is_fraud) FROM fraud_labels" in prompt
    assert "About 33% of labeled transactions are fraud." in prompt
    assert "And for Bad PIN errors?" in prompt


def test_repair_loop_recovers_from_invalid_sql(make_client):
    client, fake = make_client(
        [
            chat_reply("Counting.", "SELECT nonexistent_column FROM transactions"),
            chat_reply("Counting.", "SELECT COUNT(*) AS n FROM transactions"),
        ]
    )
    body = client.post("/api/explore", json={"message": "How many transactions?"}).json()
    assert body["status"] == "ok"
    assert body["sql"] == "SELECT COUNT(*) AS n FROM transactions"
    assert body["attempts"] == 2
    # The validation error text was fed back to the model for re-translation.
    repair_message = fake.calls[1][-1]
    assert repair_message["role"] == "user"
    assert "nonexistent_column" in repair_message["content"]


def test_repair_loop_is_bounded_at_three_attempts(make_client):
    client, fake = make_client([chat_reply("Trying.", "SELECT bad_col FROM transactions")] * 3)
    body = client.post("/api/explore", json={"message": "How many?"}).json()
    assert body["status"] == "error"
    assert body["attempts"] == 3
    assert "3 attempts" in body["message"]
    assert len(fake.calls) == 3


def test_unparseable_model_reply_is_repaired(make_client):
    client, _ = make_client(
        [
            "Sure! Here's a query: SELECT 1",
            chat_reply("Counting.", "SELECT COUNT(*) AS n FROM transactions"),
        ]
    )
    body = client.post("/api/explore", json={"message": "How many?"}).json()
    assert body["status"] == "ok"
    assert body["attempts"] == 2


def test_validation_never_executes_a_proposed_query(make_client):
    # A runaway-but-valid query must validate instantly: proposing compiles
    # only, and the timeout applies when the FSM actually runs it.
    runaway = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
        "SELECT COUNT(*) FROM c"
    )
    client, _ = make_client(
        [chat_reply("This will count forever.", runaway)], query_timeout_ms=100
    )
    body = client.post("/api/explore", json={"message": "Count to infinity"}).json()
    assert body["status"] == "ok"
    assert body["sql"] == runaway


def test_llm_unavailable_is_a_plain_english_error(make_client):
    client, _ = make_client([LLMUnavailableError("connection refused")])
    body = client.post("/api/explore", json={"message": "Anything?"}).json()
    assert body["status"] == "error"
    assert "language model" in body["message"]
    assert "connection refused" not in body["message"]


# --- Running a proposed query: the combined summarize-and-draft turn ---------


def test_run_executes_summarizes_and_drafts_a_validated_rule(make_client):
    client, fake = make_client(
        [
            rule_reply(
                "Of the 3 labeled transactions, 1 is fraud.",
                "errors = 'Bad PIN'",
                name="Bad PIN Declines",
                description="Transactions declined for a bad PIN.",
            )
        ]
    )
    body = client.post(
        "/api/explore/run",
        json={
            "sql": (
                "SELECT COUNT(*) AS labeled, SUM(f.is_fraud) AS fraud "
                "FROM transactions t JOIN fraud_labels f ON f.transaction_id = t.id"
            ),
            "history": [{"role": "user", "content": "How many are fraud?"}],
        },
    ).json()
    assert body["status"] == "ok"
    assert body["columns"] == ["labeled", "fraud"]
    assert body["rows"] == [[3, 1]]
    assert body["truncated"] is False
    assert body["summary"] == "Of the 3 labeled transactions, 1 is fraud."
    # The run turn carries the pre-validated rule artifacts (ADR-0006).
    assert body["rule"] == {
        "clause": "errors = 'Bad PIN'",
        "name": "Bad PIN Declines",
        "description": "Transactions declined for a bad PIN.",
    }
    assert body["decline_reason"] == ""
    assert body["attempts"] == 1
    # One combined call, grounded in the question, the SQL, and the real rows.
    assert len(fake.calls) == 1
    prompt = str(fake.calls[0])
    assert "How many are fraud?" in prompt
    assert "[[3, 1]]" in prompt


def test_run_drafts_from_a_plain_segment_listing(make_client):
    # ADR-0007's repro: a listing query whose rows carry no fraud column must
    # still draft — the turn translates the segment the query names (via a
    # dimension-table subquery); judging its risk is the backtest's job.
    client, _ = make_client(
        [
            rule_reply(
                "Two transactions were made at merchants in Rome.",
                "merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')",
                name="Rome Merchants",
                description="Transactions at merchants located in Rome.",
            )
        ],
        transactions=[
            (1, "2019-01-05 10:00:00", 1, 4500, "Chip Transaction", 1, 1, None),
            (2, "2019-02-01 03:00:00", 1, 82000, "Online Transaction", 1, 2, None),
            (3, "2019-03-01 12:00:00", 2, 60000, "Online Transaction", 1, 2, "Bad PIN"),
        ],
        labels=[("1", 0)],
    )
    body = client.post(
        "/api/explore/run",
        json={
            "sql": (
                "SELECT t.id, t.amount_usd_cents FROM transactions t "
                "JOIN merchant_locations ml ON ml.id = t.merchant_location_id "
                "WHERE ml.city = 'Rome' ORDER BY t.id"
            ),
            "history": [{"role": "user", "content": "List the transactions in Rome."}],
        },
    ).json()
    assert body["status"] == "ok"
    assert body["rows"] == [[2, 82000], [3, 60000]]
    assert body["rule"]["clause"] == (
        "merchant_location_id IN (SELECT id FROM merchant_locations WHERE city = 'Rome')"
    )
    assert body["decline_reason"] == ""


def test_run_returns_decline_reason_for_a_whole_table_aggregate(make_client):
    client, _ = make_client(
        [decline_reply("There are 4 transactions.", "A total count names no segment to filter on.")]
    )
    body = client.post(
        "/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"}
    ).json()
    assert body["status"] == "ok"
    assert body["rows"] == [[4]]
    assert body["summary"] == "There are 4 transactions."
    assert body["rule"] is None
    assert body["decline_reason"] == "A total count names no segment to filter on."


def test_run_prompt_replays_the_full_conversation(make_client):
    # A constraint stated in an earlier turn must reach the drafting prompt:
    # the combined turn replays the whole conversation, not just the last
    # question (the prototype's known weakness).
    client, fake = make_client([decline_reply("One online transaction.", "No risky segment.")])
    history = [
        {"role": "user", "content": "Only look at online transactions over $500."},
        {
            "role": "assistant",
            "content": "Filtered to large online transactions.",
            "sql": "SELECT COUNT(*) AS n FROM transactions WHERE amount_usd_cents > 50000",
        },
        {"role": "user", "content": "How many are there?"},
    ]
    client.post(
        "/api/explore/run",
        json={"sql": "SELECT COUNT(*) AS n FROM transactions", "history": history},
    )
    prompt = str(fake.calls[0])
    assert "Only look at online transactions over $500." in prompt
    assert "Filtered to large online transactions." in prompt
    assert "How many are there?" in prompt


def test_run_repairs_an_invalid_drafted_clause(make_client):
    client, fake = make_client(
        [
            rule_reply("One fraud.", "amount_usd_cents >>> 5"),
            rule_reply("One fraud.", "amount_usd_cents > 50000"),
        ]
    )
    body = client.post(
        "/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"}
    ).json()
    assert body["status"] == "ok"
    assert body["rule"]["clause"] == "amount_usd_cents > 50000"
    assert body["attempts"] == 2
    # The validation error was fed back for a corrected draft.
    assert "not valid" in fake.calls[1][-1]["content"]


def test_run_repairs_a_label_reading_clause(make_client):
    # The deployability ban (issue #13) applies inside the run turn: a clause
    # reading fraud_labels is rejected and the teaching message fed back.
    client, fake = make_client(
        [
            rule_reply(
                "One fraud.",
                "id IN (SELECT transaction_id FROM fraud_labels WHERE is_fraud = 1)",
            ),
            rule_reply("One fraud.", "errors = 'Bad PIN'"),
        ]
    )
    body = client.post(
        "/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"}
    ).json()
    assert body["status"] == "ok"
    assert body["rule"]["clause"] == "errors = 'Bad PIN'"
    assert body["attempts"] == 2
    assert "fraud_labels" in fake.calls[1][-1]["content"]


def test_run_drafting_exhaustion_degrades_to_summary_and_reason(make_client):
    # Rows always return: repair-loop exhaustion keeps the summary and states
    # what failed instead of hiding the evidence.
    client, _ = make_client([rule_reply("Some fraud.", "bad_col = 1")] * 3)
    body = client.post(
        "/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"}
    ).json()
    assert body["status"] == "ok"
    assert body["rows"] == [[4]]
    assert body["summary"] == "Some fraud."
    assert body["rule"] is None
    assert "3 attempts" in body["decline_reason"]
    assert body["attempts"] == 3


def test_run_prompt_pins_drafting_guidance(make_client):
    # The prompt lessons pinned deterministically (issues #12, #13, #15, #17):
    # time-of-day extraction, dollars-to-cents, the labels-never-available
    # rule, per-combination branches over cross-product IN lists, the
    # dimension-table subquery allowance for aggregate-segment evidence, and
    # ADR-0007's translation-not-judgment contract, so removing any of them
    # fails here rather than only as a metric drop on the on-demand
    # `fsm-eval rules` run.
    client, fake = make_client([decline_reply("4 transactions.", "Just a count.")])
    client.post("/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"})
    system_prompt = fake.calls[0][0]["content"]
    assert "strftime('%H', date)" in system_prompt
    assert "multiplying by 100" in system_prompt
    assert "NEVER reference `fraud_labels`" in system_prompt
    assert "AND-branch per evidenced combination" in system_prompt
    assert "SELECT id FROM merchant_locations WHERE city" in system_prompt
    assert "You do NOT decide whether that segment is risky" in system_prompt
    assert "Never decline because the rows look" in system_prompt
    assert "decline as a whole" in system_prompt


def test_run_llm_unavailable_still_returns_grounded_results(make_client):
    client, _ = make_client([LLMUnavailableError("boom")])
    body = client.post(
        "/api/explore/run", json={"sql": "SELECT COUNT(*) AS n FROM transactions"}
    ).json()
    assert body["status"] == "ok"
    assert body["rows"] == [[4]]
    assert body["summary"] == "The query ran successfully; the results are shown below."
    assert body["rule"] is None
    # The skipped draft is stated, not silent, and the raw error stays hidden.
    assert "language model" in body["decline_reason"]
    assert "boom" not in body["decline_reason"]
