from __future__ import annotations

import ast
import concurrent.futures
import inspect
import os
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from hermes_memory_provider import MnemosyneMemoryProvider, _resolve_profile
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
    *,
    provider_stub_dir: Path | None = None,
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
    cwd = ROOT
    if provider_stub_dir is not None:
        env["PYTHONPATH"] = str(ROOT)
        cwd = provider_stub_dir
    process = subprocess.Popen(
        [sys.executable, "-m", "integrations.agent_hooks.sidecar"],
        cwd=cwd,
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


def test_agent_hooks_profile_is_registered_and_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOSYNE_PREFETCH_PROFILE", "general")

    provider_cache = ProviderLRU(capacity=1)
    try:
        profile = _resolve_profile("agent-hooks")
        assert profile.top_k == 5
        assert profile.content_char_limit == 1_200
        assert os.environ["MNEMOSYNE_PREFETCH_PROFILE"] == "agent-hooks"
    finally:
        provider_cache.shutdown()


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


def test_busy_session_does_not_delay_another_session_or_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_stub_dir = tmp_path / "provider-stub"
    provider_stub_dir.mkdir()
    busy_path = tmp_path / "busy"
    release_path = tmp_path / "release"
    (provider_stub_dir / "hermes_memory_provider.py").write_text(
        textwrap.dedent(
            """
            import os
            import time
            from pathlib import Path

            class PrefetchProfile:
                def __init__(self, **_kwargs):
                    pass

            def register_profile(_profile):
                pass

            class MnemosyneMemoryProvider:
                def initialize(self, _session_id, **_kwargs):
                    pass

                def prefetch(self, prompt, *, session_id=""):
                    if prompt == "block":
                        Path(os.environ["SLOW_PROVIDER_BUSY"]).touch()
                        release = Path(os.environ["SLOW_PROVIDER_RELEASE"])
                        deadline = time.monotonic() + 3
                        while not release.exists() and time.monotonic() < deadline:
                            time.sleep(0.01)
                    return f"context for {session_id}"

                def shutdown(self):
                    pass
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SLOW_PROVIDER_BUSY", str(busy_path))
    monkeypatch.setenv("SLOW_PROVIDER_RELEASE", str(release_path))

    with _running_sidecar(
        tmp_path,
        provider_stub_dir=provider_stub_dir,
    ) as (_process, client, _data_dir):
        session_a = "codex:repository:aaaaaa"
        session_b = "codex:repository:bbbbbb"
        _assert_ok(client.prefetch("warm", session_a))

        def prefetch(prompt: str, session_id: str) -> ClientResult:
            return SidecarClient(
                socket_path=tmp_path / "sidecar.sock",
                timeout=3,
            ).prefetch(prompt, session_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            busy = executor.submit(prefetch, "block", session_a)
            deadline = time.monotonic() + 1
            while not busy_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert busy_path.exists()

            queued = executor.submit(prefetch, "queued", session_a)
            time.sleep(0.1)
            started = time.monotonic()
            unrelated = executor.submit(prefetch, "independent", session_b)
            health = executor.submit(
                SidecarClient(
                    socket_path=tmp_path / "sidecar.sock",
                    timeout=3,
                ).health
            )
            _done, delayed = concurrent.futures.wait(
                [unrelated, health],
                timeout=0.5,
            )
            elapsed = time.monotonic() - started
            release_path.touch()

            _assert_ok(busy.result(timeout=1))
            _assert_ok(queued.result(timeout=1))
            _assert_ok(unrelated.result(timeout=1))
            _assert_ok(health.result(timeout=1))

    assert not delayed, f"unrelated requests were delayed for {elapsed:.3f}s"
    assert elapsed < 0.5


def test_busy_oldest_session_keeps_provider_cache_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_stub_dir = tmp_path / "provider-stub"
    provider_stub_dir.mkdir()
    busy_path = tmp_path / "busy"
    release_path = tmp_path / "release"
    (provider_stub_dir / "hermes_memory_provider.py").write_text(
        textwrap.dedent(
            """
            import os
            import time
            from pathlib import Path

            class PrefetchProfile:
                def __init__(self, **_kwargs):
                    pass

            def register_profile(_profile):
                pass

            class MnemosyneMemoryProvider:
                def initialize(self, _session_id, **_kwargs):
                    pass

                def prefetch(self, prompt, *, session_id=""):
                    if prompt == "block":
                        Path(os.environ["SLOW_PROVIDER_BUSY"]).touch()
                        release = Path(os.environ["SLOW_PROVIDER_RELEASE"])
                        deadline = time.monotonic() + 3
                        while not release.exists() and time.monotonic() < deadline:
                            time.sleep(0.01)
                    return f"context for {session_id}"

                def shutdown(self):
                    pass
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SLOW_PROVIDER_BUSY", str(busy_path))
    monkeypatch.setenv("SLOW_PROVIDER_RELEASE", str(release_path))
    monkeypatch.setenv("MNEMOSYNE_HOOKS_PROVIDER_CACHE_SIZE", "8")

    with _running_sidecar(
        tmp_path,
        provider_stub_dir=provider_stub_dir,
    ) as (_process, client, _data_dir):
        sessions = [f"codex:repository:{index:06d}" for index in range(20)]
        for session_id in sessions[:8]:
            _assert_ok(client.prefetch("warm", session_id))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            busy = executor.submit(
                SidecarClient(
                    socket_path=tmp_path / "sidecar.sock",
                    timeout=3,
                ).prefetch,
                "block",
                sessions[0],
            )
            deadline = time.monotonic() + 1
            while not busy_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert busy_path.exists()

            try:
                for session_id in sessions[8:]:
                    _assert_ok(client.prefetch("churn", session_id))
                health = _assert_ok(client.health())
            finally:
                release_path.touch()
            _assert_ok(busy.result(timeout=1))

    assert health["live_sessions"] == 8


def test_sidecar_accepts_32_simultaneous_prefetch_requests(
    tmp_path: Path,
) -> None:
    provider_stub_dir = tmp_path / "provider-stub"
    provider_stub_dir.mkdir()
    (provider_stub_dir / "hermes_memory_provider.py").write_text(
        textwrap.dedent(
            """
            import time

            class PrefetchProfile:
                def __init__(self, **_kwargs):
                    pass

            def register_profile(_profile):
                pass

            class MnemosyneMemoryProvider:
                def initialize(self, _session_id, **_kwargs):
                    pass

                def prefetch(self, prompt, *, session_id=""):
                    if prompt == "burst":
                        time.sleep(0.2)
                    return f"context for {session_id}"

                def shutdown(self):
                    pass
            """
        ),
        encoding="utf-8",
    )

    with _running_sidecar(
        tmp_path,
        provider_stub_dir=provider_stub_dir,
    ) as (_process, client, _data_dir):
        sessions = [f"codex:repository:{index:06d}" for index in range(8)]
        for session_id in sessions:
            _assert_ok(client.prefetch("warm", session_id))

        barrier = threading.Barrier(32)

        def burst(index: int) -> ClientResult:
            barrier.wait(timeout=2)
            return SidecarClient(
                socket_path=tmp_path / "sidecar.sock",
                timeout=3,
            ).prefetch("burst", sessions[index % len(sessions)])

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            results = list(executor.map(burst, range(32)))

    failures = [result.error for result in results if not result.ok]
    assert len(results) - len(failures) == 32, failures


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
