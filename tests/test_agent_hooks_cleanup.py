from __future__ import annotations

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
    assert "OPENAI_API_KEY: 1 logical row" in result.stdout
    assert "ASSIGNED_SECRET: 1 logical row" in result.stdout
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


def test_cleanup_confirmed_run_changes_only_targeted_content(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank" / "mnemosyne.db"
    db_path.parent.mkdir()
    _seed_bank(db_path)
    controls_before = {
        table: next(row for row in _rows(db_path, table) if row[0] == "control")
        for table in TABLES
    }

    result = _run_cleanup(db_path, "--apply", "--confirm", "CLEANUP")

    assert result.returncode == 0, result.stderr
    assert "APPLIED: Bank cleanup committed" in result.stdout
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
        assert by_id["control"] == controls_before[table]
        assert by_id["control"][4] == "claude-code:repo:control"


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
