"""The app-owned state database.

Application state (saved Rules and their Backtest snapshots, from the P1
slice onward) lives here, never in the provided dataset. The guarded
exploration connection never attaches this file, so LLM-generated SQL cannot
see application state.
"""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

# A saved Rule persists its clause, name, description, and the Backtest snapshot
# it was approved with (PRD stories 21-22). The snapshot is the full backtest
# result as JSON; `score` is denormalized into its own column so the Rules tab
# can rank by Score without parsing every snapshot.
_CREATE_RULES = """\
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    clause TEXT NOT NULL,
    score REAL NOT NULL,
    backtest_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""


def init_app_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute(_CREATE_RULES)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (SCHEMA_VERSION,)
        )
        conn.commit()
    finally:
        conn.close()
