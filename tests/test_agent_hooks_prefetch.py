from __future__ import annotations

import ast
import concurrent.futures
import inspect
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from hermes_memory_provider import MnemosyneMemoryProvider
from integrations.agent_hooks.client import ClientResult, SidecarClient
from integrations.agent_hooks.provider_cache import ProviderLRU
from integrations.agent_hooks.transport import (
    HOOK_TIMEOUT_SECONDS,
    MAX_INJECTION_CHARS,
    SOCKET_ENV,
)
from mnemosyne import Mnemosyne


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _redirect_memory_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "test-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "test-hermes-home"))
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path / "mnemosyne-data"))
    monkeypatch.setenv("MNEMOSYNE_NO_EMBEDDINGS", "1")


@contextmanager
def _running_sidecar(
    tmp_path: Path,
) -> Iterator[tuple[subprocess.Popen[str], SidecarClient, Path]]:
    socket_path = tmp_path / "sidecar.sock"
    data_dir = tmp_path / "mnemosyne-data"
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"
    env[SOCKET_ENV] = str(socket_path)
    env.pop("HERMES_HOME", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "integrations.agent_hooks.sidecar"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    client = SidecarClient(socket_path=socket_path, timeout=3)
    try:
        deadline = time.monotonic() + 5
        health = client.health()
        while not health.ok and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Sidecar exited with {process.returncode}: "
                    f"stdout={stdout!r}, stderr={stderr!r}"
                )
            time.sleep(0.01)
            health = client.health()
        assert health.ok, health.error
        yield process, client, data_dir
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)


def _seed_memory(
    data_dir: Path,
    *,
    session_id: str,
    content: str,
    source: str = "correction",
    importance: float = 0.95,
    scope: str = "global",
) -> str:
    memory = Mnemosyne(
        session_id=session_id,
        db_path=data_dir / "mnemosyne.db",
    )
    return memory.remember(
        content,
        source=source,
        importance=importance,
        scope=scope,
    )


def _assert_ok(result: ClientResult) -> dict[str, object]:
    assert result.ok, result.error
    assert result.status == 200
    assert result.data is not None
    return result.data


def test_prefetch_surfaces_global_memory_from_another_repository(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mnemosyne-data"
    _seed_memory(
        data_dir,
        session_id="claude-code:unrelated-repository:a1b2c3",
        content=(
            "Agent Hook Injection must resolve linked worktrees through "
            "git's common directory."
        ),
    )

    with _running_sidecar(tmp_path) as (_process, client, _data_dir):
        response = _assert_ok(
            client.prefetch(
                "How should Agent Hook Injection resolve linked worktrees?",
                "codex:different-repository:d4e5f6",
            )
        )
        health = _assert_ok(client.health())

    assert "git's common directory" in response["context"]
    assert health["live_sessions"] == 1


def test_provider_contract_matches_sidecar_calls() -> None:
    assert str(inspect.signature(MnemosyneMemoryProvider.initialize)) == (
        "(self, session_id: 'str', **kwargs) -> 'None'"
    )
    assert str(inspect.signature(MnemosyneMemoryProvider.prefetch)) == (
        "(self, query: 'str', *, session_id: 'str' = '') -> 'str'"
    )
    assert str(inspect.signature(MnemosyneMemoryProvider.sync_turn)) == (
        "(self, user_content: 'str', assistant_content: 'str', "
        "*, session_id: 'str' = '') -> 'None'"
    )

    source = textwrap.dedent(inspect.getsource(ProviderLRU._create))
    tree = ast.parse(source)
    initialize_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "initialize"
    )
    keywords = {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in initialize_call.keywords
    }
    assert keywords == {
        "default_scope": "global",
        "agent_context": "primary",
    }


def test_prefetch_keeps_distilled_memory_and_provider_deduplicates_noise(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mnemosyne-data"
    phrase = "Agent Hook routing must preserve repository provenance"
    _seed_memory(
        data_dir,
        session_id="source-repository",
        content=f"{phrase} through the Session id.",
        source="correction",
        importance=0.85,
    )
    _seed_memory(
        data_dir,
        session_id="source-repository",
        content=f"[USER] {phrase} through the Session id",
        source="conversation",
        importance=0.95,
    )
    _seed_memory(
        data_dir,
        session_id="source-repository",
        content=f"[ASSISTANT] {phrase} and narrate every implementation detail",
        source="conversation",
        importance=1.0,
    )

    with _running_sidecar(tmp_path) as (_process, client, _data_dir):
        response = _assert_ok(
            client.prefetch(
                "How must Agent Hook routing preserve repository provenance?",
                "codex:target-repository:112233",
            )
        )

    context = str(response["context"])
    assert "through the Session id." in context
    assert "[USER]" not in context
    assert "[ASSISTANT]" not in context
    assert context.count(phrase) == 1


def test_concurrent_sessions_receive_only_their_own_identity(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mnemosyne-data"
    sessions = {
        "claude-code:repo:aaa111": "Claude session identity: likes cobalt.",
        "codex:repo:bbb222": "Codex session identity: likes vermilion.",
    }
    for session, identity in sessions.items():
        _seed_memory(
            data_dir,
            session_id=f"hermes_{session}",
            content=identity,
            source="identity",
            scope="session",
        )

    with _running_sidecar(tmp_path) as (_process, client, _data_dir):
        for session, identity in sessions.items():
            response = _assert_ok(client.prefetch("Hello", session))
            assert identity in response["context"]

        def request(session: str) -> tuple[str, str]:
            result = SidecarClient(
                socket_path=tmp_path / "sidecar.sock",
                timeout=3,
            ).prefetch("Hello again", session)
            return session, str(_assert_ok(result)["context"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(
                    request,
                    [*sessions, *sessions, *sessions, *sessions],
                )
            )
        health = _assert_ok(client.health())

    for session, context in responses:
        assert sessions[session] in context
        other_identity = next(
            identity
            for other_session, identity in sessions.items()
            if other_session != session
        )
        assert other_identity not in context
    assert health["live_sessions"] == 2


def test_prefetch_context_is_provider_capped_and_hard_bounded(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mnemosyne-data"
    long_tail = " highly specific injection detail" * 300
    for index in range(8):
        _seed_memory(
            data_dir,
            session_id="foreign-repository",
            content=f"Agent Hook cap memory {index}:{long_tail}",
            source="correction",
            importance=0.9,
        )

    session = "codex:target-repository:c0ffee"
    with _running_sidecar(tmp_path) as (_process, client, _data_dir):
        response = _assert_ok(
            client.prefetch("Agent Hook cap highly specific injection detail", session)
        )
        context = str(response["context"])
        assert 0 < len(context) < MAX_INJECTION_CHARS
        assert context.count("Agent Hook cap memory") <= 5
        assert max(len(line) for line in context.splitlines()) <= 1_400

        for index in range(12):
            _seed_memory(
                data_dir,
                session_id=f"hermes_{session}",
                content=f"Identity {index}:{long_tail}",
                source="identity",
                scope="session",
            )
        oversized = _assert_ok(client.prefetch("Hello", session))

    assert oversized["context"] == ""


def test_socket_timeout_returns_failure_and_sidecar_stays_live(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "mnemosyne-data"
    _seed_memory(
        data_dir,
        session_id="latency-source",
        content="Warm Prefetch latency should remain below the Hook timeout.",
    )

    with _running_sidecar(tmp_path) as (_process, client, _data_dir):
        _assert_ok(
            client.prefetch(
                "Warm Prefetch latency",
                "codex:latency-repository:445566",
            )
        )
        tiny_timeout_client = SidecarClient(
            socket_path=tmp_path / "sidecar.sock",
            timeout=0.000001,
        )
        result = tiny_timeout_client.prefetch(
            "Warm Prefetch latency",
            "codex:latency-repository:445566",
        )
        assert result.ok is False
        assert result.error

        deadline = time.monotonic() + HOOK_TIMEOUT_SECONDS
        health = client.health()
        while not health.ok and time.monotonic() < deadline:
            time.sleep(0.01)
            health = client.health()

    assert health.ok
