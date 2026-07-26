"""Warm Provider LRU owned by the Agent Hooks Sidecar."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from hermes_memory_provider import (
    MnemosyneMemoryProvider,
    PrefetchProfile,
    register_profile,
)

from .transport import MAX_INJECTION_CHARS


_PROFILE_NAME = "agent-hooks"
_MAX_PROVIDER_CONTEXT_CHARS = MAX_INJECTION_CHARS - 64
_DEFAULT_CAPACITY = 8


@dataclass
class _Entry:
    provider: MnemosyneMemoryProvider
    lock: threading.Lock = field(default_factory=threading.Lock)


class ProviderLRU:
    """Keep independently initialized Providers warm by Session id."""

    def __init__(self, capacity: int | None = None) -> None:
        register_profile(
            PrefetchProfile(
                name=_PROFILE_NAME,
                top_k=5,
                content_char_limit=1_200,
            )
        )
        os.environ.setdefault("MNEMOSYNE_PREFETCH_PROFILE", _PROFILE_NAME)
        if capacity is None:
            try:
                capacity = int(
                    os.environ.get(
                        "MNEMOSYNE_HOOKS_PROVIDER_CACHE_SIZE",
                        str(_DEFAULT_CAPACITY),
                    )
                )
            except ValueError:
                capacity = _DEFAULT_CAPACITY
        self._capacity = max(1, capacity)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def _create(self, session_id: str) -> _Entry:
        provider = MnemosyneMemoryProvider()
        provider.initialize(
            session_id,
            default_scope="global",
            agent_context="primary",
        )
        return _Entry(provider)

    @contextmanager
    def _lease(self, session_id: str) -> Iterator[MnemosyneMemoryProvider]:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = self._create(session_id)
                self._entries[session_id] = entry
                if len(self._entries) > self._capacity:
                    old_session_id, old_entry = next(iter(self._entries.items()))
                    if old_session_id != session_id:
                        old_entry.lock.acquire()
                        try:
                            self._entries.pop(old_session_id)
                            old_entry.provider.shutdown()
                        finally:
                            old_entry.lock.release()
            else:
                self._entries.move_to_end(session_id)
            entry.lock.acquire()
        try:
            yield entry.provider
        finally:
            entry.lock.release()

    def prefetch(self, prompt: str, session_id: str) -> str:
        with self._lease(session_id) as provider:
            context = provider.prefetch(prompt, session_id=session_id)
        if len(context) > _MAX_PROVIDER_CONTEXT_CHARS:
            return ""
        return context

    @property
    def live_sessions(self) -> int:
        with self._lock:
            return len(self._entries)

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            with entry.lock:
                entry.provider.shutdown()
