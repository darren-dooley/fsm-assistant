"""Test wiring for the PRD's two seams: the in-process HTTP API (primary)
and the injectable LLM client (the only test double).

The fixture dataset is hand-computable: with the test cutoff of 2019-09-01
the visible slice holds 4 transactions, 3 labels, and 1 known fraud; a fifth
post-cutoff transaction (id 5, labeled fraud) must never be visible.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsm_assistant.api import create_app
from fsm_assistant.config import Settings
from fsm_assistant.evals.fixture import build_dataset_db

CUTOFF = "2019-09-01"

FIXTURE_TRANSACTIONS = [
    # id, date, card_id, amount_usd_cents, transaction_type, merchant_id, merchant_location_id, errors
    (1, "2019-01-15 10:00:00", 1, 10000, "Online Transaction", 1, 1, None),
    (2, "2019-02-01 03:00:00", 1, 60000, "Online Transaction", 1, 1, "Bad PIN"),
    (3, "2019-03-01 12:00:00", 2, 5000, "Swipe Transaction", 1, 1, None),
    (4, "2019-04-01 15:00:00", 2, 70000, "Online Transaction", 1, 1, None),  # unlabeled
    (5, "2019-09-15 03:00:00", 1, 99999, "Online Transaction", 1, 1, "Bad PIN"),  # post-cutoff
]

FIXTURE_LABELS = [("1", 0), ("2", 1), ("3", 0), ("5", 1)]


def build_fixture_data_db(path: Path) -> None:
    build_dataset_db(path, FIXTURE_TRANSACTIONS, FIXTURE_LABELS)


class FakeLLM:
    """Scripted LLM client: returns (or raises) each response in order and
    records every prompt it was sent."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def chat_reply(reply: str, sql: str | None = None) -> str:
    payload: dict[str, str] = {"reply": reply}
    if sql is not None:
        payload["sql"] = sql
    return json.dumps(payload)


def rule_reply(
    summary: str,
    clause: str,
    name: str = "Suggested Rule",
    description: str = "It matches the evidenced transactions.",
) -> str:
    return json.dumps(
        {"summary": summary, "rule": {"clause": clause, "name": name, "description": description}}
    )


def decline_reply(summary: str, reason: str) -> str:
    return json.dumps({"summary": summary, "decline": reason})


@pytest.fixture
def make_client(tmp_path):
    def _make(
        responses: list,
        *,
        transactions: list | None = None,
        labels: list | None = None,
        **settings_overrides,
    ) -> tuple[TestClient, FakeLLM]:
        # A custom dataset lives at its own path so it never collides with the
        # default fixture's cached db within one test.
        if transactions is not None or labels is not None:
            data_db = tmp_path / "custom.db"
            if not data_db.exists():
                build_dataset_db(data_db, transactions or [], labels or [])
        else:
            data_db = tmp_path / "data.db"
            if not data_db.exists():
                build_fixture_data_db(data_db)
        settings = Settings(
            data_db_path=data_db,
            app_db_path=tmp_path / "app.db",
            cache_db_path=tmp_path / "cache.db",
            cutoff=CUTOFF,
            **settings_overrides,
        )
        fake = FakeLLM(responses)
        return TestClient(create_app(settings, fake)), fake

    return _make
