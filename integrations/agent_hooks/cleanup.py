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


@dataclass(frozen=True)
class CleanupPlan:
    """An immutable report and mutation plan for one Bank snapshot."""

    db_path: Path
    actions: tuple[RowAction, ...]

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


def _plan_cleanup(
    connection: sqlite3.Connection,
    db_path: Path,
) -> CleanupPlan:
    actions: list[RowAction] = []
    for table in _existing_memory_tables(connection):
        rows = connection.execute(
            f"SELECT id, content FROM {table} ORDER BY id"
        ).fetchall()
        for memory_id, content in rows:
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
                    )
                )
    return CleanupPlan(db_path=db_path, actions=tuple(actions))


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


def _print_plan(plan: CleanupPlan, output: TextIO) -> None:
    print(f"Bank: {plan.db_path}", file=output)
    print(_format_action_count("Pseudo-prompt rows", plan.pseudo_prompt_rows), file=output)
    print(_format_action_count("Credential-shaped rows", plan.credential_rows), file=output)
    print(_format_action_count("Would remove", plan.pseudo_prompt_rows), file=output)
    print(_format_action_count("Would redact", plan.redaction_rows), file=output)
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


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _apply_plan(connection: sqlite3.Connection, plan: CleanupPlan) -> None:
    for action in plan.actions:
        if action.pseudo_prompt:
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
        connection = sqlite3.connect(db_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan = _plan_cleanup(connection, db_path)
            _print_plan(plan, output)
            _apply_plan(connection, plan)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
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
