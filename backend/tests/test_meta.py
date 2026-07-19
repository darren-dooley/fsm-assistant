"""The meta endpoint: the base fraud rate reference point (story 9) and the
label-coverage facts the UI shows alongside it (story 8)."""


def test_meta_reports_cutoff_and_base_fraud_rate(make_client):
    client, _ = make_client([])
    body = client.get("/api/meta").json()
    assert body["cutoff"] == "2019-09-01"
    assert body["transactions"] == 4
    assert body["labeled"] == 3
    assert body["fraud"] == 1
    assert body["label_coverage_pct"] == 75.0
    assert body["base_fraud_rate_pct"] == 33.333
    assert body["row_limit"] == 200
    assert body["query_timeout_ms"] == 5000
