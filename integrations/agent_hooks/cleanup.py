"""One-shot cleanup of stored Agent Hook pseudo-prompts and credentials."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

_hygiene = importlib.import_module(
    f"{__package__}.hygiene" if __package__ else "hygiene"
)


_MEMORY_TABLES = ("memories", "working_memory")
_CHILD_MEMORY_TABLES = ("memory_embeddings", "annotations", "gists")
_USER_PREFIX = re.compile(r"\A\[USER\][ \t]*")
_REDACTION_LABEL = re.compile(r"\[REDACTED:([A-Z_]+)\]")
_CONFIRMATION = "CLEANUP"


@dataclass(frozen=True)
class RowAction:
    """A planned change to one physical Bank row."""

    table: str
    memory_id: str
    content: str
    redacted_content: str
    pseudo_prompt: bool
    credential_shaped: bool
    working_rowid: int | None


@dataclass(frozen=True)
class CleanupPlan:
    """An immutable report and mutation plan for one Bank snapshot."""

    db_path: Path
    actions: tuple[RowAction, ...]
    child_row_counts: tuple[tuple[str, int], ...]

    @property
    def pseudo_prompt_rows(self) -> tuple[RowAction, ...]:
        return tuple(action for action in self.actions if action.pseudo_prompt)

    @property
    def credential_rows(self) -> tuple[RowAction, ...]:
        return tuple(action for action in self.actions if action.credential_shaped)

    @property
    def redaction_rows(self) -> tuple[RowAction, ...]:
        return tuple(
            action
            for action in self.actions
            if action.credential_shaped and not action.pseudo_prompt
        )

    @property
    def overlapping_ids(self) -> set[str]:
        return {
            action.memory_id
            for action in self.actions
            if action.pseudo_prompt and action.credential_shaped
        }

    @property
    def label_counts(self) -> Counter[str]:
        labels_by_id: dict[str, set[str]] = {}
        for action in self.credential_rows:
            labels_by_id.setdefault(action.memory_id, set()).update(
                _REDACTION_LABEL.findall(action.redacted_content)
            )
        return Counter(
            label for labels in labels_by_id.values() for label in labels
        )


def _default_db_path() -> Path:
    data_dir = os.environ.get("MNEMOSYNE_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "mnemosyne.db"
    hermes_home = os.environ.get("HERMES_HOME")
    root = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return root / "mnemosyne" / "data" / "mnemosyne.db"


def _is_cleanup_pseudo_prompt(content: str) -> bool:
    """Use the shared detector only when its wrapper opens a stored user row."""

    prefix = _USER_PREFIX.match(content)
    if prefix is None:
        return False
    body = content[prefix.end() :].lstrip()
    if not body.startswith("<"):
        return False
    opening_tag_end = body.find(">")
    if opening_tag_end < 0:
        return False
    return _hygiene.is_pseudo_prompt(body[: opening_tag_end + 1])


def _existing_memory_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        table
        for table in _MEMORY_TABLES
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _child_row_counts(
    connection: sqlite3.Connection,
    actions: list[RowAction],
) -> tuple[tuple[str, int], ...]:
    target_ids = {
        action.memory_id for action in actions if action.pseudo_prompt
    }
    counts: list[tuple[str, int]] = []
    for table in _CHILD_MEMORY_TABLES:
        count = 0
        if target_ids and _table_exists(connection, table):
            rows = connection.execute(
                f"SELECT memory_id, COUNT(*) FROM {table} GROUP BY memory_id"
            ).fetchall()
            count = sum(
                int(row_count)
                for memory_id, row_count in rows
                if str(memory_id) in target_ids
            )
        counts.append((table, count))

    target_rowids = {
        action.working_rowid
        for action in actions
        if action.pseudo_prompt and action.working_rowid is not None
    }
    vec_count = 0
    if target_rowids and _table_exists(connection, "vec_working"):
        vec_count = sum(
            1
            for (rowid,) in connection.execute(
                "SELECT rowid FROM vec_working"
            ).fetchall()
            if int(rowid) in target_rowids
        )
    counts.append(("vec_working", vec_count))
    return tuple(counts)


def _plan_cleanup(
    connection: sqlite3.Connection,
    db_path: Path,
) -> CleanupPlan:
    actions: list[RowAction] = []
    for table in _existing_memory_tables(connection):
        rows = connection.execute(
            f"SELECT rowid, id, content FROM {table} ORDER BY id"
        ).fetchall()
        for rowid, memory_id, content in rows:
            if not isinstance(content, str) or _USER_PREFIX.match(content) is None:
                continue
            pseudo_prompt = _is_cleanup_pseudo_prompt(content)
            redacted_content = _hygiene.redact_credentials(content)
            credential_shaped = redacted_content != content
            if pseudo_prompt or credential_shaped:
                actions.append(
                    RowAction(
                        table=table,
                        memory_id=str(memory_id),
                        content=content,
                        redacted_content=redacted_content,
                        pseudo_prompt=pseudo_prompt,
                        credential_shaped=credential_shaped,
                        working_rowid=(
                            int(rowid) if table == "working_memory" else None
                        ),
                    )
                )
    return CleanupPlan(
        db_path=db_path,
        actions=tuple(actions),
        child_row_counts=_child_row_counts(connection, actions),
    )


def _logical_count(actions: tuple[RowAction, ...]) -> int:
    return len({action.memory_id for action in actions})


def _noun(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _format_action_count(label: str, actions: tuple[RowAction, ...]) -> str:
    logical = _logical_count(actions)
    physical = len(actions)
    return (
        f"{label}: {logical} logical {_noun(logical, 'ID')}, "
        f"{physical} table {_noun(physical, 'row')}"
    )


def _print_table_counts(
    label: str,
    actions: tuple[RowAction, ...],
    output: TextIO,
) -> None:
    print(f"{label} by table:", file=output)
    counts = Counter(action.table for action in actions)
    for table in _MEMORY_TABLES:
        count = counts.get(table, 0)
        print(
            f"  {table}: {count} table {_noun(count, 'row')}",
            file=output,
        )


def _print_plan(
    plan: CleanupPlan,
    output: TextIO,
    *,
    applied: bool = False,
) -> None:
    print(f"Bank: {plan.db_path}", file=output)
    print(_format_action_count("Pseudo-prompt rows", plan.pseudo_prompt_rows), file=output)
    print(_format_action_count("Credential-shaped rows", plan.credential_rows), file=output)
    remove_label = "Removed" if applied else "Would remove"
    redact_label = "Redacted" if applied else "Would redact"
    print(_format_action_count(remove_label, plan.pseudo_prompt_rows), file=output)
    _print_table_counts(remove_label, plan.pseudo_prompt_rows, output)
    print(_format_action_count(redact_label, plan.redaction_rows), file=output)
    _print_table_counts(redact_label, plan.redaction_rows, output)
    overlap = len(plan.overlapping_ids)
    print(
        f"Both pseudo-prompt and credential-shaped: {overlap} logical "
        f"{_noun(overlap, 'ID')}",
        file=output,
    )
    print(
        "Credential labels (final placeholders; ASSIGNED_SECRET can mask a "
        "more specific earlier label):",
        file=output,
    )
    if plan.label_counts:
        for label, count in sorted(plan.label_counts.items()):
            print(
                f"  {label}: {count} logical {_noun(count, 'row')}",
                file=output,
            )
    else:
        print("  none", file=output)
    child_label = "Child rows removed" if applied else "Child rows to remove"
    print(f"{child_label}:", file=output)
    for table, count in plan.child_row_counts:
        print(f"  {table}: {count} {_noun(count, 'row')}", file=output)
    print(
        "Limitations: credential detection does not cover quoted JSON keys, "
        "Authorization: Basic, URL userinfo, or prose separators such as "
        "PASSWORD IS and password → value.",
        file=output,
    )
    print(
        "A redaction can consume a non-secret token that merely follows "
        "password: (for example, ssm:GetParameter).",
        file=output,
    )
    print(
        "[ASSISTANT] rows are not included in this targeted cleanup.",
        file=output,
    )


def _load_optional_sqlite_vec(connection: sqlite3.Connection) -> None:
    try:
        sqlite_vec = importlib.import_module("sqlite_vec")
    except ModuleNotFoundError as error:
        if error.name == "sqlite_vec":
            return
        raise
    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    _load_optional_sqlite_vec(connection)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _open_read_write(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()))}?mode=rw"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    _load_optional_sqlite_vec(connection)
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _apply_plan(connection: sqlite3.Connection, plan: CleanupPlan) -> None:
    for action in plan.actions:
        if action.pseudo_prompt:
            for child_table in _CHILD_MEMORY_TABLES:
                connection.execute(
                    f"DELETE FROM {child_table} WHERE memory_id = ?",
                    (action.memory_id,),
                )
            if action.working_rowid is not None:
                try:
                    connection.execute(
                        "DELETE FROM vec_working WHERE rowid = ?",
                        (action.working_rowid,),
                    )
                except sqlite3.OperationalError as error:
                    if "no such table" not in str(error).lower():
                        raise
            cursor = connection.execute(
                f"DELETE FROM {action.table} WHERE id = ? AND content = ?",
                (action.memory_id, action.content),
            )
        else:
            cursor = connection.execute(
                f"""
                UPDATE {action.table}
                SET content = ?
                WHERE id = ? AND content = ?
                """,
                (
                    action.redacted_content,
                    action.memory_id,
                    action.content,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"Bank row changed while cleanup was running: "
                f"{action.table}/{action.memory_id}"
            )


def run_cleanup(
    db_path: Path,
    *,
    apply: bool,
    output: TextIO = sys.stdout,
) -> CleanupPlan:
    """Report a Bank cleanup plan and optionally commit it atomically."""

    if apply:
        connection = _open_read_write(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if not _existing_memory_tables(connection):
                raise RuntimeError(
                    "Bank has neither memories nor working_memory"
                )
            plan = _plan_cleanup(connection, db_path)
            _apply_plan(connection, plan)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        _print_plan(plan, output, applied=True)
        print("APPLIED: Bank cleanup committed", file=output)
        return plan

    with _open_read_only(db_path) as connection:
        connection.execute("BEGIN")
        plan = _plan_cleanup(connection, db_path)
        connection.rollback()
    _print_plan(plan, output)
    print("DRY RUN: no Bank rows changed", file=output)
    return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report stored Agent Hook pseudo-prompts and credential shapes. "
            "Dry-run only unless explicitly confirmed."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_default_db_path(),
        help="Bank database path (default: active default Bank)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the reported cleanup in one transaction",
    )
    parser.add_argument(
        "--confirm",
        metavar=_CONFIRMATION,
        help=f"typed confirmation required with --apply: {_CONFIRMATION}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.apply and args.confirm != _CONFIRMATION:
        parser.error(f"--confirm {_CONFIRMATION} is required with --apply")
    if args.confirm is not None and not args.apply:
        parser.error("--confirm is only valid with --apply")
    try:
        run_cleanup(args.db, apply=args.apply)
    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(f"cleanup failed; no Bank changes committed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
