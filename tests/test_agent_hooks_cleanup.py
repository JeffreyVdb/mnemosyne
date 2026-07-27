from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from mnemosyne import Mnemosyne


ROOT = Path(__file__).resolve().parents[1]
TABLES = ("memories", "working_memory")
OPENAI_KEY = "sk-" + ("a" * 24)
PORTAL_PASSWORD = "correct-horse-battery-staple"


def _seed_bank(db_path: Path) -> None:
    Mnemosyne(session_id="schema", db_path=db_path)
    rows = (
        (
            "pseudo",
            "[USER] <system-reminder>Host-generated instructions</system-reminder>",
            "claude-code:repo:pseudo",
        ),
        (
            "credential-only",
            f"[USER] {OPENAI_KEY}",
            "codex:repo:credential",
        ),
        (
            "credential-with-content",
            (
                f"[USER] School portal password: {PORTAL_PASSWORD}; "
                "the course schedule is otherwise useful."
            ),
            "codex:repo:credential",
        ),
        (
            "control",
            "[USER] Discuss <system-reminder> tags without opening with one.",
            "claude-code:repo:control",
        ),
    )
    with sqlite3.connect(db_path) as connection:
        for table in TABLES:
            connection.executemany(
                f"""
                INSERT INTO {table}
                    (id, content, source, timestamp, session_id, importance,
                     metadata_json)
                VALUES (?, ?, 'conversation', '2026-07-26T12:00:00',
                        ?, 0.8, '{{}}')
                """,
                rows,
            )


def _rows(db_path: Path, table: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            f"SELECT * FROM {table} ORDER BY id"
        ).fetchall()


def _row_by_id(db_path: Path, table: str, memory_id: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT * FROM {table} WHERE id = ?",
            (memory_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _run_cleanup(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "integrations.agent_hooks.cleanup",
            "--db",
            str(db_path),
            *args,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _seed_cleanup_children(db_path: Path, memory_id: str) -> None:
    with sqlite3.connect(db_path) as connection:
        sqlite_vec = importlib.import_module("sqlite_vec")
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        finally:
            connection.enable_load_extension(False)
        working_rowid = connection.execute(
            "SELECT rowid FROM working_memory WHERE id = ?",
            (memory_id,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO memory_embeddings (memory_id, embedding_json, model)
            VALUES (?, '[0.0]', 'test')
            """,
            (memory_id,),
        )
        connection.execute(
            """
            INSERT INTO annotations (memory_id, kind, value, source)
            VALUES (?, 'entity', 'Host payload', 'test')
            """,
            (memory_id,),
        )
        connection.execute(
            """
            INSERT INTO gists
                (id, text, timestamp, participants_json, memory_id)
            VALUES ('gist-pseudo', '<system-reminder>Host payload',
                    '2026-07-26T12:00:00', '["Background"]', ?)
            """,
            (memory_id,),
        )
        connection.execute("DROP TABLE IF EXISTS vec_working")
        connection.execute(
            "CREATE TABLE vec_working (rowid INTEGER PRIMARY KEY, embedding BLOB)"
        )
        connection.execute(
            "INSERT INTO vec_working (rowid, embedding) VALUES (?, X'00')",
            (working_rowid,),
        )


def _cleanup_orphan_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        return {
            "memory_embeddings": connection.execute(
                """
                SELECT COUNT(*) FROM memory_embeddings child
                WHERE NOT EXISTS (
                    SELECT 1 FROM memories WHERE id = child.memory_id
                    UNION ALL
                    SELECT 1 FROM working_memory WHERE id = child.memory_id
                )
                """
            ).fetchone()[0],
            "annotations": connection.execute(
                """
                SELECT COUNT(*) FROM annotations child
                WHERE NOT EXISTS (
                    SELECT 1 FROM memories WHERE id = child.memory_id
                    UNION ALL
                    SELECT 1 FROM working_memory WHERE id = child.memory_id
                )
                """
            ).fetchone()[0],
            "gists": connection.execute(
                """
                SELECT COUNT(*) FROM gists child
                WHERE NOT EXISTS (
                    SELECT 1 FROM memories WHERE id = child.memory_id
                    UNION ALL
                    SELECT 1 FROM working_memory WHERE id = child.memory_id
                )
                """
            ).fetchone()[0],
            "vec_working": connection.execute(
                """
                SELECT COUNT(*) FROM vec_working child
                WHERE NOT EXISTS (
                    SELECT 1 FROM working_memory parent
                    WHERE parent.rowid = child.rowid
                )
                """
            ).fetchone()[0],
        }


def _snapshot_bank(db_path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(db_path) as connection:
        sqlite_vec = importlib.import_module("sqlite_vec")
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        finally:
            connection.enable_load_extension(False)
        tables = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table'
                ORDER BY name
                """
            ).fetchall()
        ]
        return {
            table: sorted(
                connection.execute(f'SELECT * FROM "{table}"').fetchall(),
                key=repr,
            )
            for table in tables
        }


def test_cleanup_dry_run_reports_plan_without_changing_bank(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    before = {table: _rows(db_path, table) for table in TABLES}

    result = _run_cleanup(db_path)

    assert result.returncode == 0, result.stderr
    assert "DRY RUN: no Bank rows changed" in result.stdout
    assert "Pseudo-prompt rows: 1 logical ID, 2 table rows" in result.stdout
    assert "Credential-shaped rows: 2 logical IDs, 4 table rows" in result.stdout
    assert "Would remove: 1 logical ID, 2 table rows" in result.stdout
    assert "Would redact: 2 logical IDs, 4 table rows" in result.stdout
    assert "Would remove by table:" in result.stdout
    assert "memories: 1 table row" in result.stdout
    assert "working_memory: 1 table row" in result.stdout
    assert "OPENAI_API_KEY: 1 logical row" in result.stdout
    assert "ASSIGNED_SECRET: 1 logical row" in result.stdout
    assert "quoted JSON keys" in result.stdout
    assert "Authorization: Basic" in result.stdout
    assert "URL userinfo" in result.stdout
    assert "PASSWORD IS" in result.stdout
    assert "password → value" in result.stdout
    assert "non-secret token" in result.stdout
    assert "ssm:GetParameter" in result.stdout
    assert "[ASSISTANT] rows are not included" in result.stdout
    assert {table: _rows(db_path, table) for table in TABLES} == before


def test_cleanup_requires_typed_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    before = {table: _rows(db_path, table) for table in TABLES}

    result = _run_cleanup(db_path, "--apply")

    assert result.returncode == 2
    assert "--confirm CLEANUP is required" in result.stderr
    assert {table: _rows(db_path, table) for table in TABLES} == before


def test_cleanup_rejects_confirmation_without_apply(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    before = {table: _rows(db_path, table) for table in TABLES}

    result = _run_cleanup(db_path, "--confirm", "CLEANUP")

    assert result.returncode == 2
    assert "--confirm is only valid with --apply" in result.stderr
    assert {table: _rows(db_path, table) for table in TABLES} == before


def test_cleanup_apply_refuses_missing_bank_without_creating_it(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mistyped" / "mnemosyne.db"
    db_path.parent.mkdir()

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 1
    assert "cleanup failed; no Bank changes committed" in result.stderr
    assert "unable to open database file" in result.stderr
    assert not db_path.exists()


def test_cleanup_apply_refuses_database_without_memory_tables(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 1
    assert "Bank has neither memories nor working_memory" in result.stderr
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall() == [("unrelated",)]


def test_cleanup_locked_bank_exits_without_committing(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    before = _snapshot_bank(db_path)

    with sqlite3.connect(db_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        lock.execute(
            "UPDATE memories SET importance = importance WHERE id = 'control'"
        )
        result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 1
    assert "database is locked" in result.stderr
    assert _snapshot_bank(db_path) == before


def test_cleanup_rolls_back_injected_mid_pass_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    rows = [
        (
            f"a{index:03d}",
            "[USER] <task-notification>generated target</task-notification>",
            f"codex:repo:rollback-{index:03d}",
        )
        for index in range(531)
    ]
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO memories
                (id, content, source, timestamp, session_id, importance,
                 metadata_json)
            VALUES (?, ?, 'conversation', '2026-07-26T12:00:00',
                    ?, 0.8, '{}')
            """,
            rows,
        )
        connection.executescript(
            """
            CREATE TRIGGER cleanup_abort_266
            BEFORE DELETE ON memories
            WHEN OLD.id = 'a265'
            BEGIN
                SELECT RAISE(ABORT, 'injected mid-pass failure');
            END;
            """
        )
    before = _snapshot_bank(db_path)
    assert len(before) >= 53

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 1
    assert "injected mid-pass failure" in result.stderr
    assert _snapshot_bank(db_path) == before


def test_cleanup_rolls_back_when_target_rowcount_is_not_one(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TRIGGER cleanup_remove_before_delete
            BEFORE DELETE ON memories
            WHEN OLD.id = 'pseudo'
            BEGIN
                DELETE FROM memories WHERE id = OLD.id;
                SELECT RAISE(IGNORE);
            END;
            """
        )
    before = _snapshot_bank(db_path)

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 1
    assert "Bank row changed while cleanup was running" in result.stderr
    assert _snapshot_bank(db_path) == before


def test_cleanup_confirmed_run_changes_only_targeted_content(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    controls_before = {
        table: _row_by_id(db_path, table, "control") for table in TABLES
    }

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "APPLIED: Bank cleanup committed" in result.stdout
    assert "Removed: 1 logical ID, 2 table rows" in result.stdout
    assert "Redacted: 2 logical IDs, 4 table rows" in result.stdout
    assert "Would remove" not in result.stdout
    assert "Would redact" not in result.stdout
    for table in TABLES:
        rows = _rows(db_path, table)
        by_id = {str(row[0]): row for row in rows}
        assert "pseudo" not in by_id
        assert OPENAI_KEY not in str(by_id["credential-only"])
        assert "[REDACTED:OPENAI_API_KEY]" in str(by_id["credential-only"])
        assert PORTAL_PASSWORD not in str(by_id["credential-with-content"])
        assert "the course schedule is otherwise useful" in str(
            by_id["credential-with-content"]
        )
        control = _row_by_id(db_path, table, "control")
        assert control == controls_before[table]
        assert control["session_id"] == "claude-code:repo:control"


def test_cleanup_leaves_matching_episodic_memory_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO episodic_memory
                (id, content, source, timestamp, session_id, importance,
                 metadata_json)
            VALUES ('episodic-wrapper',
                    '[USER] <system-reminder>distilled wrapper</system-reminder>',
                    'sleep_consolidation', '2026-07-26T12:00:00',
                    'codex:repo:episodic', 0.9, '{}')
            """
        )
    before = _row_by_id(db_path, "episodic_memory", "episodic-wrapper")

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert _row_by_id(db_path, "episodic_memory", "episodic-wrapper") == before


def test_cleanup_deletes_row_present_in_only_one_mirror(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO memories
                (id, content, source, timestamp, session_id, importance,
                 metadata_json)
            VALUES ('legacy-only',
                    '[USER] <task-notification>legacy only</task-notification>',
                    'conversation', '2026-07-26T12:00:00',
                    'claude-code:repo:legacy-only', 0.8, '{}')
            """
        )

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "memories: 2 table rows" in result.stdout
    assert "working_memory: 1 table row" in result.stdout
    assert all(row[0] != "legacy-only" for row in _rows(db_path, "memories"))


def test_cleanup_anchor_keeps_non_opening_wrappers(tmp_path: Path) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    controls = (
        (
            "assistant-wrapper",
            "[ASSISTANT] <task-notification>quoted Host text</task-notification>",
        ),
        (
            "markdown-wrapper",
            "[USER] > <system-reminder>quoted wrapper</system-reminder>",
        ),
        (
            "later-wrapper",
            "[USER] <custom-tag>first</custom-tag> then "
            "<task-notification>later</task-notification>",
        ),
    )
    with sqlite3.connect(db_path) as connection:
        for table in TABLES:
            connection.executemany(
                f"""
                INSERT INTO {table}
                    (id, content, source, timestamp, session_id, importance,
                     metadata_json)
                VALUES (?, ?, 'conversation', '2026-07-26T12:00:00',
                        'codex:repo:anchor-control', 0.8, '{{}}')
                """,
                controls,
            )
    before = {table: _rows(db_path, table) for table in TABLES}

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    for table in TABLES:
        surviving = {str(row[0]): row for row in _rows(db_path, table)}
        for memory_id, _content in controls:
            assert surviving[memory_id] in before[table]


def test_cleanup_handles_bank_without_legacy_memories_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE memories")

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "working_memory: 1 table row" in result.stdout
    assert all(row[0] != "pseudo" for row in _rows(db_path, "working_memory"))


def test_cleanup_deletes_pseudo_prompt_that_also_has_credential(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        for table in TABLES:
            connection.execute(
                f"""
                INSERT INTO {table}
                    (id, content, source, timestamp, session_id, importance,
                     metadata_json)
                VALUES ('both', ?, 'conversation', '2026-07-26T12:00:00',
                        'codex:repo:both', 0.8, '{{}}')
                """,
                (
                    f"[USER] <task-notification>{OPENAI_KEY}"
                    "</task-notification>",
                ),
            )

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "Both pseudo-prompt and credential-shaped: 1 logical ID" in result.stdout
    for table in TABLES:
        assert all(row[0] != "both" for row in _rows(db_path, table))


def test_cleanup_cascades_target_children_and_reports_counts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    _seed_cleanup_children(db_path, "pseudo")

    report = _run_cleanup(db_path)

    assert report.returncode == 0, report.stderr
    assert "Child rows to remove:" in report.stdout
    assert "memory_embeddings: 1 row" in report.stdout
    assert "annotations: 1 row" in report.stdout
    assert "gists: 1 row" in report.stdout
    assert "vec_working: 1 row" in report.stdout

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert _cleanup_orphan_counts(db_path) == {
        "memory_embeddings": 0,
        "annotations": 0,
        "gists": 0,
        "vec_working": 0,
    }
    with sqlite3.connect(db_path) as connection:
        for table in (
            "memory_embeddings",
            "annotations",
            "gists",
            "vec_working",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0


def test_cleanup_cascade_tolerates_missing_optional_vec_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    with sqlite3.connect(db_path) as connection:
        sqlite_vec = importlib.import_module("sqlite_vec")
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
            connection.execute("DROP TABLE vec_working")
        finally:
            connection.enable_load_extension(False)

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "vec_working: 0 rows" in result.stdout
    assert all(row[0] != "pseudo" for row in _rows(db_path, "working_memory"))
