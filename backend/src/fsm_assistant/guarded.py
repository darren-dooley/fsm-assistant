"""The guarded executor: the only path to the provided dataset.

Every query runs on a read-only connection where temp views shadow
`transactions` and `fraud_labels` with pre-cutoff versions (ADR-0004), an
authorizer allows nothing but reading, a progress handler enforces the
timeout, and fetching stops at the row cap.

`fraud_labels.transaction_id` is declared TEXT in the provided dataset while
`transactions.id` is INTEGER, so a direct join degenerates to a full scan of
the label table per transaction row (hours, not seconds). The executor
therefore builds a one-off cache database — an app-owned file, the provided
dataset is never written — holding the pre-cutoff labels with an INTEGER
primary key, and the `fraud_labels` view reads from it.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import Settings

# Authorizer action codes permitted for exploration. Everything else —
# writes, DDL, PRAGMA, ATTACH, transaction control — is denied.
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}

_PROGRESS_HANDLER_INTERVAL_OPS = 10_000

# SQLite primary result codes (stable constants, not exposed by the sqlite3
# module): SQLITE_INTERRUPT is raised when the progress handler aborts a
# query; SQLITE_AUTH when the authorizer denies one.
_SQLITE_INTERRUPT = 9
_SQLITE_AUTH = 23

# Tables whose raw main-schema versions contain post-cutoff rows. Reading
# them schema-qualified (main.transactions) would bypass the pre-T temp
# views, so the authorizer only permits reading them through the views. The
# label cache needs no such rule: it holds pre-cutoff labels only, so every
# path to it is sealed by construction.
_SEALED_TABLES = ("transactions", "fraud_labels")

# The statement shape a Rule clause actually runs in. The backtest builds its
# aggregate SELECT over this FROM, and clause validation compiles the same
# shape, so a clause can never pass validation and then fail the backtest
# (or vice versa) because the two wrapped it differently. `transactions` is
# deliberately unaliased so table-qualified references like
# `transactions.errors` resolve identically everywhere.
RULE_EVAL_FROM = (
    "FROM transactions LEFT JOIN fraud_labels f ON f.transaction_id = transactions.id"
)

_NOT_DEPLOYABLE_MESSAGE = (
    "A rule scores new, unlabeled transactions, so it cannot read "
    "fraud_labels. Express the pattern as conditions on transaction "
    "attributes only — e.g. name the high-risk segment values directly "
    "(transaction_type = '...', merchant_location_id IN (...))."
)


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


ErrorKind = Literal["sqlite_error", "unsafe", "timeout"]


class GuardedQueryError(Exception):
    """A query was rejected or failed. `kind` distinguishes repairable SQL
    errors ("sqlite_error", "unsafe") from the terminal "timeout"."""

    def __init__(self, message: str, kind: ErrorKind):
        super().__init__(message)
        self.kind: ErrorKind = kind


def _strip_leading_comments(sql: str) -> str:
    text = sql.lstrip()
    while True:
        if text.startswith("--"):
            newline = text.find("\n")
            if newline == -1:
                return ""
            text = text[newline + 1 :].lstrip()
        elif text.startswith("/*"):
            end = text.find("*/")
            if end == -1:
                return ""
            text = text[end + 2 :].lstrip()
        else:
            return text


class GuardedExecutor:
    def __init__(self, settings: Settings):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", settings.cutoff):
            raise ValueError(f"Cutoff must be YYYY-MM-DD, got {settings.cutoff!r}")
        self._settings = settings
        self._ensure_label_cache()

    def execute(self, sql: str) -> QueryResult:
        sql = sql.strip().rstrip(";").strip()
        head = _strip_leading_comments(sql)
        if not head or head.split(None, 1)[0].upper() not in ("SELECT", "WITH"):
            raise GuardedQueryError(
                "Only a single read-only SELECT statement is allowed.", kind="unsafe"
            )

        conn = self._connect()
        try:
            deadline = time.monotonic() + self._settings.query_timeout_ms / 1000

            def _abort_if_past_deadline() -> int:
                return 1 if time.monotonic() > deadline else 0

            conn.set_progress_handler(_abort_if_past_deadline, _PROGRESS_HANDLER_INTERVAL_OPS)
            conn.set_authorizer(self._authorize)
            try:
                cursor = conn.execute(sql)
                fetched = cursor.fetchmany(self._settings.row_limit + 1)
            except sqlite3.ProgrammingError as exc:
                # Python's sqlite3 refuses multi-statement strings outright.
                raise GuardedQueryError(
                    "Only a single SQL statement is allowed.", kind="unsafe"
                ) from exc
            except sqlite3.DatabaseError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                if code == _SQLITE_INTERRUPT:
                    raise GuardedQueryError(
                        f"The query was cut off after "
                        f"{self._settings.query_timeout_ms / 1000:g} seconds.",
                        kind="timeout",
                    ) from exc
                if code == _SQLITE_AUTH:
                    raise GuardedQueryError(
                        "Only read-only SELECT queries over the exploration "
                        "tables are allowed.",
                        kind="unsafe",
                    ) from exc
                raise GuardedQueryError(str(exc), kind="sqlite_error") from exc

            columns = [d[0] for d in cursor.description] if cursor.description else []
            truncated = len(fetched) > self._settings.row_limit
            rows = [list(row) for row in fetched[: self._settings.row_limit]]
            return QueryResult(columns=columns, rows=rows, truncated=truncated)
        finally:
            conn.close()

    def validate_sql(self, sql: str) -> None:
        """Compile-check a full SELECT without executing it.

        Runs the same head check as `execute`, then prepares the statement as
        an `EXPLAIN` against the read-only database under the same authorizer.
        Preparation resolves every table and column and catches syntax errors
        and unsafe operations, and stepping an EXPLAIN emits opcodes rather
        than running the query — so even a runaway query validates instantly.
        Chat-proposed SQL passes through here before it is shown with a Run
        button; actual execution happens only via `execute` when the FSM runs
        it.

        Raises GuardedQueryError on any malformed or disallowed statement.
        """
        sql = sql.strip().rstrip(";").strip()
        head = _strip_leading_comments(sql)
        if not head or head.split(None, 1)[0].upper() not in ("SELECT", "WITH"):
            raise GuardedQueryError(
                "Only a single read-only SELECT statement is allowed.", kind="unsafe"
            )
        self._compile_check(
            f"EXPLAIN {sql}",
            multi_statement_message="Only a single SQL statement is allowed.",
            auth_message="Only read-only SELECT queries over the exploration tables are allowed.",
        )

    def validate_clause(self, clause: str) -> None:
        """Compile-check a Rule's WHERE clause without executing it.

        Two compile-only passes, no scan ever runs. The parentheses both scope
        the boolean expression and make an unbalanced clause fail here rather
        than silently changing the query.

        1. Deployability: the clause alone, under an authorizer that denies
           every read of `fraud_labels`. A rule scores new transactions that
           have no label yet, so a clause that reads labels (through any path
           — subquery, view, cache schema) is rejected with a teaching
           message, not deployed to look impressive in backtests and do
           nothing in production.
        2. Shape parity: the clause compiled in the exact statement shape the
           backtest runs (`RULE_EVAL_FROM`), so column resolution — including
           correlated subqueries — behaves identically at validation and at
           backtest time.

        Raises GuardedQueryError (kind "unsafe" or "sqlite_error") on any
        malformed or disallowed clause; returns None when the clause is a valid,
        safe WHERE expression. Drafted and hand-edited clauses pass this
        identically (ADR-0003, PRD stories 13/39).
        """
        stripped = clause.strip()
        if not stripped:
            raise GuardedQueryError("The rule clause is empty.", kind="unsafe")
        self._compile_check(
            f"EXPLAIN SELECT id FROM transactions WHERE ({clause})",
            multi_statement_message="A rule clause must be a single boolean expression.",
            auth_message=_NOT_DEPLOYABLE_MESSAGE,
            authorizer=self._authorize_deployable,
        )
        self._compile_check(
            f"EXPLAIN SELECT COUNT(*) {RULE_EVAL_FROM} WHERE ({clause})",
            multi_statement_message="A rule clause must be a single boolean expression.",
            auth_message="A rule clause may only read the transaction tables.",
        )

    def _compile_check(
        self,
        explain_sql: str,
        *,
        multi_statement_message: str,
        auth_message: str,
        authorizer=None,
    ) -> None:
        conn = self._connect()
        try:
            conn.set_authorizer(authorizer or self._authorize)
            try:
                conn.execute(explain_sql)
            except sqlite3.ProgrammingError as exc:
                raise GuardedQueryError(multi_statement_message, kind="unsafe") from exc
            except sqlite3.DatabaseError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                if code == _SQLITE_AUTH:
                    raise GuardedQueryError(auth_message, kind="unsafe") from exc
                raise GuardedQueryError(str(exc), kind="sqlite_error") from exc
        finally:
            conn.close()

    def schema_ddl(self) -> str:
        """The dataset's table DDL, for the translator's prompt."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT sql FROM main.sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            return ";\n".join(row[0] for row in rows if row[0])
        finally:
            conn.close()

    @staticmethod
    def _authorize(action: int, arg1, arg2, db_name, view) -> int:
        if action not in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if (
            action == sqlite3.SQLITE_READ
            and db_name == "main"
            and arg1 in _SEALED_TABLES
            and view not in _SEALED_TABLES
        ):
            # Direct access to a sealed table's raw schema-qualified form;
            # `view` names the temp view mediating the read, None when the
            # query touches the table itself.
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def _authorize_deployable(action: int, arg1, arg2, db_name, view) -> int:
        # The deployability authorizer for Rule clauses: everything the
        # ordinary authorizer denies, plus every read of fraud_labels —
        # whether named directly, reached through the temp view, or read from
        # the label cache schema underneath it.
        if action == sqlite3.SQLITE_READ and (
            arg1 == "fraud_labels" or view == "fraud_labels" or db_name == "label_cache"
        ):
            return sqlite3.SQLITE_DENY
        return GuardedExecutor._authorize(action, arg1, arg2, db_name, view)

    def _connect(self) -> sqlite3.Connection:
        settings = self._settings
        conn = sqlite3.connect(f"file:{settings.data_db_path}?mode=ro", uri=True)
        conn.execute(f"ATTACH DATABASE 'file:{settings.cache_db_path}?mode=ro' AS label_cache")
        # Temp views shadow the underlying tables: unqualified names resolve
        # to the temp schema first, so generated SQL only ever sees pre-T data.
        # View definitions cannot take bound parameters; the cutoff is
        # format-validated in __init__ before it is interpolated here.
        conn.execute(
            "CREATE TEMP VIEW transactions AS "
            f"SELECT * FROM main.transactions WHERE date < '{settings.cutoff}'"
        )
        conn.execute(
            "CREATE TEMP VIEW fraud_labels AS "
            "SELECT transaction_id, is_fraud FROM label_cache.fraud_labels"
        )
        return conn

    def _ensure_label_cache(self) -> None:
        settings = self._settings
        cache_path = Path(settings.cache_db_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        source_stat = Path(settings.data_db_path).stat()
        fingerprint = f"{settings.cutoff}|{source_stat.st_size}|{source_stat.st_mtime_ns}"

        conn = sqlite3.connect(f"file:{cache_path}", uri=True)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            existing = conn.execute(
                "SELECT value FROM cache_meta WHERE key = 'fingerprint'"
            ).fetchone()
            if existing and existing[0] == fingerprint:
                return
            conn.execute(f"ATTACH DATABASE 'file:{settings.data_db_path}?mode=ro' AS src")
            # `main.` qualifiers throughout: unqualified names would resolve
            # to the attached read-only dataset once its tables are in scope.
            conn.execute("DROP TABLE IF EXISTS main.fraud_labels")
            conn.execute(
                "CREATE TABLE main.fraud_labels "
                "(transaction_id INTEGER PRIMARY KEY, is_fraud BOOLEAN)"
            )
            conn.execute(
                "INSERT INTO main.fraud_labels "
                "SELECT CAST(f.transaction_id AS INTEGER), f.is_fraud "
                "FROM src.fraud_labels f "
                "JOIN src.transactions t ON t.id = CAST(f.transaction_id AS INTEGER) "
                "WHERE t.date < ?",
                (settings.cutoff,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO cache_meta VALUES ('fingerprint', ?)", (fingerprint,)
            )
            conn.commit()
        finally:
            conn.close()
