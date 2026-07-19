"""Application settings, resolved from environment variables with local defaults."""

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Every value can be overridden via FSM_*/OPENAI_* env vars."""

    data_db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("FSM_DATA_DB", REPO_ROOT / "data" / "data.db")
    ))
    app_db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("FSM_APP_DB", REPO_ROOT / "backend" / "var" / "app.db")
    ))
    cache_db_path: Path = field(default_factory=lambda: Path(
        os.environ.get("FSM_CACHE_DB", REPO_ROOT / "backend" / "var" / "cache.db")
    ))
    # The global cutoff T (ADR-0004): authoring surfaces only ever see rows
    # strictly before this date. 2019-09-01 keeps ~80% of transactions,
    # labels, and known fraud on the visible side.
    cutoff: str = field(default_factory=lambda: os.environ.get("FSM_CUTOFF", "2019-09-01"))
    row_limit: int = field(default_factory=lambda: int(os.environ.get("FSM_ROW_LIMIT", "200")))
    query_timeout_ms: int = field(default_factory=lambda: int(os.environ.get("FSM_QUERY_TIMEOUT_MS", "5000")))
    max_translation_attempts: int = field(default_factory=lambda: int(os.environ.get("FSM_MAX_ATTEMPTS", "3")))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-5-nano"))
