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
    provider: MnemosyneMemoryProvider | None = None
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
        os.environ["MNEMOSYNE_PREFETCH_PROFILE"] = _PROFILE_NAME
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

    def _create(self, session_id: str) -> MnemosyneMemoryProvider:
        provider = MnemosyneMemoryProvider()
        provider.initialize(
            session_id,
            default_scope="global",
            agent_context="primary",
        )
        return provider

    def _entry_for(self, session_id: str) -> _Entry:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _Entry()
                self._entries[session_id] = entry
            else:
                self._entries.move_to_end(session_id)
        return entry

    def _evict_excess(self) -> None:
        while True:
            with self._lock:
                if len(self._entries) <= self._capacity:
                    return
                old_entry = None
                for old_session_id, candidate in tuple(self._entries.items()):
                    if candidate.lock.acquire(blocking=False):
                        old_entry = candidate
                        self._entries.pop(old_session_id)
                        break
                if old_entry is None:
                    return
            try:
                if old_entry.provider is not None:
                    old_entry.provider.shutdown()
            finally:
                old_entry.lock.release()

    @contextmanager
    def _lease(self, session_id: str) -> Iterator[MnemosyneMemoryProvider]:
        while True:
            entry = self._entry_for(session_id)
            entry.lock.acquire()
            with self._lock:
                if self._entries.get(session_id) is entry:
                    break
            entry.lock.release()
        try:
            if entry.provider is None:
                try:
                    entry.provider = self._create(session_id)
                except BaseException:
                    with self._lock:
                        if self._entries.get(session_id) is entry:
                            self._entries.pop(session_id)
                    raise
            yield entry.provider
        finally:
            entry.lock.release()
            self._evict_excess()

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
                if entry.provider is not None:
                    entry.provider.shutdown()
