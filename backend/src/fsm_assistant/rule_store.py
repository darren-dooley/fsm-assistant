"""Persistence for the saved rule set.

Saved Rules live in the app-owned database (never the provided dataset). Each
row carries the exact clause and the Backtest snapshot it was saved with, so
the rule set is reviewable as a whole and ranked by Score (PRD stories 22-25).
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .backtest import BacktestResult


@dataclass(frozen=True)
class SavedRule:
    id: int
    name: str
    description: str
    clause: str
    score: float
    backtest: dict
    created_at: str
    updated_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "clause": self.clause,
            "score": self.score,
            "backtest": self.backtest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class RuleStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, name: str, description: str, clause: str, backtest: BacktestResult) -> SavedRule:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO rules (name, description, clause, score, backtest_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, description, clause, backtest.score, json.dumps(backtest.as_dict())),
            )
            conn.commit()
            return self._require(conn, cursor.lastrowid)
        finally:
            conn.close()

    def list(self) -> list[SavedRule]:
        conn = self._connect()
        try:
            # Ranked by Score, newest first as a stable tiebreak.
            rows = conn.execute(
                "SELECT * FROM rules ORDER BY score DESC, id DESC"
            ).fetchall()
            return [self._row_to_rule(row) for row in rows]
        finally:
            conn.close()

    def get(self, rule_id: int) -> SavedRule | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
            return self._row_to_rule(row) if row else None
        finally:
            conn.close()

    def update(
        self, rule_id: int, name: str, description: str, clause: str, backtest: BacktestResult
    ) -> SavedRule | None:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE rules SET name = ?, description = ?, clause = ?, score = ?, "
                "backtest_json = ?, updated_at = datetime('now') WHERE id = ?",
                (name, description, clause, backtest.score, json.dumps(backtest.as_dict()), rule_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            return self._require(conn, rule_id)
        finally:
            conn.close()

    def refresh_snapshot(self, rule_id: int, backtest: BacktestResult) -> SavedRule | None:
        """Re-backtest on demand: replace the snapshot for an unchanged clause
        (PRD story 24). Deterministic, so the numbers reproduce; the timestamp
        records when it was last verified."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE rules SET score = ?, backtest_json = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (backtest.score, json.dumps(backtest.as_dict()), rule_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            return self._require(conn, rule_id)
        finally:
            conn.close()

    def delete(self, rule_id: int) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def _require(self, conn: sqlite3.Connection, rule_id: int) -> SavedRule:
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return self._row_to_rule(row)

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> SavedRule:
        return SavedRule(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            clause=row["clause"],
            score=row["score"],
            backtest=json.loads(row["backtest_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
