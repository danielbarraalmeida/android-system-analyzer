"""SQLite-backed knowledge store with optional vector search.

Embeddings are stored as JSON arrays in TEXT columns — keeps the file
portable and dependency-free. Cosine similarity is computed in pure
Python (numpy used only if available, but never required).

All upserts use ``ON CONFLICT … DO UPDATE`` so re-indexing a device on
later sessions enriches existing rows instead of duplicating them.

The store is a thin persistence layer. Higher-level concerns (text
serialisation, embedding) live in :mod:`indexer` and :mod:`retriever`.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    serial            TEXT PRIMARY KEY,
    first_seen_utc    TEXT NOT NULL,
    last_seen_utc     TEXT NOT NULL,
    manufacturer      TEXT,
    model             TEXT,
    android_version   TEXT,
    sdk_int           INTEGER,
    build_fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS properties (
    serial         TEXT NOT NULL,
    key            TEXT NOT NULL,
    value          TEXT,
    last_seen_utc  TEXT NOT NULL,
    PRIMARY KEY (serial, key)
);

CREATE TABLE IF NOT EXISTS packages (
    serial         TEXT NOT NULL,
    package        TEXT NOT NULL,
    apk_path       TEXT,
    is_system      INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER,
    version_name   TEXT,
    version_code   INTEGER,
    permissions    TEXT,
    activity_count INTEGER,
    last_seen_utc  TEXT NOT NULL,
    text_repr      TEXT,
    embedding      TEXT,
    PRIMARY KEY (serial, package)
);

CREATE TABLE IF NOT EXISTS services (
    serial         TEXT NOT NULL,
    service        TEXT NOT NULL,
    interface      TEXT,
    last_seen_utc  TEXT NOT NULL,
    PRIMARY KEY (serial, service)
);

CREATE TABLE IF NOT EXISTS settings (
    serial         TEXT NOT NULL,
    namespace      TEXT NOT NULL,
    key            TEXT NOT NULL,
    value          TEXT,
    last_seen_utc  TEXT NOT NULL,
    PRIMARY KEY (serial, namespace, key)
);

CREATE TABLE IF NOT EXISTS dumpsys_excerpts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    serial        TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    section       TEXT NOT NULL,
    raw_file      TEXT,
    captured_utc  TEXT NOT NULL,
    text_repr     TEXT,
    embedding     TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    serial        TEXT NOT NULL,
    category      TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    recorded_utc  TEXT NOT NULL,
    text_repr     TEXT,
    embedding     TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_serial_cat ON facts(serial, category);

CREATE TABLE IF NOT EXISTS screen_snapshots (
    serial          TEXT NOT NULL,
    signature       TEXT NOT NULL,
    package         TEXT,
    activity        TEXT,
    screenshot_path TEXT,
    session_id      TEXT NOT NULL,
    captured_utc    TEXT NOT NULL,
    PRIMARY KEY (serial, signature)
);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    serial      TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    chunk       INTEGER NOT NULL,
    text_repr   TEXT NOT NULL,
    embedding   TEXT,
    recorded_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_serial ON findings(serial);
"""


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _dump_vec(vec: Sequence[float] | None) -> str | None:
    if vec is None:
        return None
    return json.dumps([float(x) for x in vec])


def _load_vec(blob: str | None) -> list[float] | None:
    if not blob:
        return None
    try:
        return [float(x) for x in json.loads(blob)]
    except (ValueError, TypeError):
        return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na  += x * x
        nb  += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    table:   str
    row_id:  Any
    score:   float
    payload: dict[str, Any]


class KnowledgeStore:
    """Thin SQLite wrapper. Construct once per process, share across runs."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        # ``:memory:`` is tolerated for tests.
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Generic
    # ------------------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Device + properties
    # ------------------------------------------------------------------
    def upsert_device(
        self,
        *,
        serial: str,
        manufacturer: str | None = None,
        model: str | None = None,
        android_version: str | None = None,
        sdk_int: int | None = None,
        build_fingerprint: str | None = None,
    ) -> None:
        now = _now_utc()
        self.conn.execute(
            """
            INSERT INTO device (serial, first_seen_utc, last_seen_utc,
                                manufacturer, model, android_version,
                                sdk_int, build_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(serial) DO UPDATE SET
                last_seen_utc     = excluded.last_seen_utc,
                manufacturer      = COALESCE(excluded.manufacturer, device.manufacturer),
                model             = COALESCE(excluded.model, device.model),
                android_version   = COALESCE(excluded.android_version, device.android_version),
                sdk_int           = COALESCE(excluded.sdk_int, device.sdk_int),
                build_fingerprint = COALESCE(excluded.build_fingerprint, device.build_fingerprint)
            """,
            (serial, now, now, manufacturer, model, android_version,
             sdk_int, build_fingerprint),
        )
        self.conn.commit()

    def upsert_property(self, serial: str, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO properties (serial, key, value, last_seen_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(serial, key) DO UPDATE SET
                value         = excluded.value,
                last_seen_utc = excluded.last_seen_utc
            """,
            (serial, key, value, _now_utc()),
        )

    def upsert_properties(self, serial: str, props: dict[str, str]) -> None:
        for k, v in props.items():
            self.upsert_property(serial, k, v)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Packages
    # ------------------------------------------------------------------
    def upsert_package(
        self,
        *,
        serial: str,
        package: str,
        apk_path: str | None = None,
        is_system: bool = False,
        enabled: bool | None = None,
        version_name: str | None = None,
        version_code: int | None = None,
        permissions: Iterable[str] | None = None,
        activity_count: int | None = None,
        text_repr: str | None = None,
        embedding: Sequence[float] | None = None,
    ) -> None:
        perms_json = json.dumps(sorted(set(permissions))) if permissions is not None else None
        self.conn.execute(
            """
            INSERT INTO packages (serial, package, apk_path, is_system, enabled,
                                  version_name, version_code, permissions,
                                  activity_count, last_seen_utc, text_repr, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(serial, package) DO UPDATE SET
                apk_path       = COALESCE(excluded.apk_path, packages.apk_path),
                is_system      = excluded.is_system OR packages.is_system,
                enabled        = COALESCE(excluded.enabled, packages.enabled),
                version_name   = COALESCE(excluded.version_name, packages.version_name),
                version_code   = COALESCE(excluded.version_code, packages.version_code),
                permissions    = COALESCE(excluded.permissions, packages.permissions),
                activity_count = COALESCE(excluded.activity_count, packages.activity_count),
                last_seen_utc  = excluded.last_seen_utc,
                text_repr      = COALESCE(excluded.text_repr, packages.text_repr),
                embedding      = COALESCE(excluded.embedding, packages.embedding)
            """,
            (serial, package, apk_path, int(is_system),
             None if enabled is None else int(enabled),
             version_name, version_code, perms_json, activity_count,
             _now_utc(), text_repr, _dump_vec(embedding)),
        )

    # ------------------------------------------------------------------
    # Services / settings
    # ------------------------------------------------------------------
    def upsert_service(self, serial: str, service: str, interface: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO services (serial, service, interface, last_seen_utc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(serial, service) DO UPDATE SET
                interface     = excluded.interface,
                last_seen_utc = excluded.last_seen_utc
            """,
            (serial, service, interface, _now_utc()),
        )

    def upsert_setting(self, serial: str, namespace: str, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO settings (serial, namespace, key, value, last_seen_utc)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(serial, namespace, key) DO UPDATE SET
                value         = excluded.value,
                last_seen_utc = excluded.last_seen_utc
            """,
            (serial, namespace, key, value, _now_utc()),
        )

    # ------------------------------------------------------------------
    # Dumpsys / facts / findings / screens
    # ------------------------------------------------------------------
    def insert_dumpsys_excerpt(
        self,
        *,
        serial: str,
        session_id: str,
        section: str,
        raw_file: str | None,
        captured_utc: str,
        text_repr: str,
        embedding: Sequence[float] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO dumpsys_excerpts (serial, session_id, section,
                                          raw_file, captured_utc, text_repr, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (serial, session_id, section, raw_file, captured_utc,
             text_repr, _dump_vec(embedding)),
        )
        return int(cur.lastrowid)

    def insert_fact(
        self,
        *,
        serial: str,
        session_id: str,
        category: str,
        key: str,
        value: str,
        recorded_utc: str,
        text_repr: str,
        embedding: Sequence[float] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO facts (serial, session_id, category, key, value,
                               recorded_utc, text_repr, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (serial, session_id, category, key, value, recorded_utc,
             text_repr, _dump_vec(embedding)),
        )
        return int(cur.lastrowid)

    def insert_finding(
        self,
        *,
        serial: str,
        session_id: str,
        chunk: int,
        text_repr: str,
        embedding: Sequence[float] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO findings (serial, session_id, chunk, text_repr,
                                  embedding, recorded_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (serial, session_id, chunk, text_repr,
             _dump_vec(embedding), _now_utc()),
        )
        return int(cur.lastrowid)

    def upsert_screen_snapshot(
        self,
        *,
        serial: str,
        signature: str,
        session_id: str,
        package: str | None,
        activity: str | None,
        screenshot_path: str | None,
        captured_utc: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO screen_snapshots (serial, signature, package, activity,
                                          screenshot_path, session_id, captured_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(serial, signature) DO UPDATE SET
                package         = COALESCE(excluded.package, screen_snapshots.package),
                activity        = COALESCE(excluded.activity, screen_snapshots.activity),
                screenshot_path = COALESCE(excluded.screenshot_path, screen_snapshots.screenshot_path),
                session_id      = excluded.session_id,
                captured_utc    = excluded.captured_utc
            """,
            (serial, signature, package, activity, screenshot_path,
             session_id, captured_utc),
        )

    def commit(self) -> None:
        self.conn.commit()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def get_device(self, serial: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM device WHERE serial = ?", (serial,),
        ).fetchone()
        return dict(row) if row else None

    def get_known_facts(self, serial: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, category, key, value, recorded_utc, session_id
              FROM facts WHERE serial = ?
             ORDER BY recorded_utc DESC LIMIT ?
            """,
            (serial, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_known_packages(self, serial: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT package FROM packages WHERE serial = ? ORDER BY package",
            (serial,),
        ).fetchall()
        return [r["package"] for r in rows]

    def count_table(self, table: str) -> int:
        # Whitelisted: tests-only convenience.
        allowed = {"device", "properties", "packages", "services", "settings",
                   "dumpsys_excerpts", "facts", "findings", "screen_snapshots"}
        if table not in allowed:
            raise ValueError(f"unknown table {table!r}")
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------
    def cosine_search(
        self,
        *,
        table: str,
        query_embedding: Sequence[float],
        serial: str | None = None,
        top_k: int = 5,
    ) -> list[SearchHit]:
        """Brute-force cosine ranking over rows with non-null embeddings."""
        spec = _SEARCHABLE.get(table)
        if spec is None:
            raise ValueError(f"table {table!r} is not searchable")
        sql = (
            f"SELECT {spec['id']} AS row_id, text_repr, embedding"
            f"{', ' + ', '.join(spec['extra']) if spec['extra'] else ''}"
            f"  FROM {table} WHERE embedding IS NOT NULL"
        )
        params: list[Any] = []
        if serial is not None and "serial" in spec["filterable"]:
            sql += " AND serial = ?"
            params.append(serial)
        rows = self.conn.execute(sql, params).fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            vec = _load_vec(row["embedding"])
            if not vec:
                continue
            score = _cosine(query_embedding, vec)
            payload = {k: row[k] for k in row.keys() if k != "embedding"}
            hits.append(SearchHit(table=table, row_id=row["row_id"],
                                  score=score, payload=payload))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


_SEARCHABLE: dict[str, dict[str, Any]] = {
    "facts": {
        "id":         "id",
        "extra":      ["serial", "category", "key", "value", "recorded_utc"],
        "filterable": {"serial"},
    },
    "findings": {
        "id":         "id",
        "extra":      ["serial", "session_id", "chunk", "recorded_utc"],
        "filterable": {"serial"},
    },
    "dumpsys_excerpts": {
        "id":         "id",
        "extra":      ["serial", "section", "captured_utc"],
        "filterable": {"serial"},
    },
    "packages": {
        "id":         "package",
        "extra":      ["serial", "version_name", "is_system"],
        "filterable": {"serial"},
    },
}
