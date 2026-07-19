"""Builds a stand-in for the provided dataset: the real schema, minimal
supporting rows for referential columns, and caller-supplied transactions and
labels. The deterministic test suite builds its tiny fixture through this,
and the rule-quality eval builds its hand-computable pattern database."""

import sqlite3
from pathlib import Path

from ..config import REPO_ROOT


def build_dataset_db(path: Path, transactions: list, labels: list) -> None:
    conn = sqlite3.connect(path)
    schema = (REPO_ROOT / "data" / "schema.sql").read_text()
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO users VALUES (1, '1980-05-01', 'Female', '1 Main St', 0, 0, "
        "3000000, 6000000, 100000, 700)"
    )
    conn.execute("INSERT INTO mcc_codes VALUES (5411, 'Grocery Stores')")
    conn.execute("INSERT INTO merchants VALUES (1, 'Acme Mart', 5411)")
    conn.execute("INSERT INTO merchant_locations VALUES (1, 1, 'Springfield', 'IL', 62701)")
    conn.execute("INSERT INTO merchant_locations VALUES (2, 1, 'Rome', 'NY', 13440)")
    conn.execute(
        "INSERT INTO cards VALUES (1, 1, 'Visa', 'Credit', '2024-01-01', 1, 500000, "
        "'2015-01-01', 2018, 0)"
    )
    conn.execute(
        "INSERT INTO cards VALUES (2, 1, 'Mastercard', 'Debit', '2025-01-01', 1, 300000, "
        "'2016-01-01', 2019, 0)"
    )
    conn.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", transactions)
    conn.executemany("INSERT INTO fraud_labels VALUES (?,?)", labels)
    conn.commit()
    conn.close()
