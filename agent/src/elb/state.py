"""Per-direction turn state machine.

    LISTENING -> PROCESSING -> SPEAKING -> LISTENING
                     |
                     +--(timeout)--> FALLBACK -> SPEAKING -> LISTENING

FALLBACK exists so a slow or dead STT/LLM degrades into "the other side hears
the original words" rather than "the call silently hangs". In an emergency,
untranslated audio beats no audio.

This is deliberately turn-based. Barge-in / simultaneous duplex is out of
scope: it would require echo separation per direction and buys nothing for the
thing we are actually demonstrating.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class TurnState(str, Enum):
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    FALLBACK = "fallback"
    ERROR = "error"


@dataclass
class TurnMetrics:
    started_at: float = 0.0
    stt_final_at: float = 0.0
    translated_at: float = 0.0
    audio_started_at: float = 0.0
    fell_back: bool = False
    repaired_terms: list[str] = field(default_factory=list)

    @property
    def translate_ms(self) -> int:
        if not (self.stt_final_at and self.translated_at):
            return 0
        return int((self.translated_at - self.stt_final_at) * 1000)

    @property
    def end_to_end_ms(self) -> int:
        if not (self.stt_final_at and self.audio_started_at):
            return 0
        return int((self.audio_started_at - self.stt_final_at) * 1000)


StateListener = Callable[[str, TurnState, TurnMetrics], Awaitable[None] | None]


class DirectionState:
    """Tracks one translation direction and notifies listeners on transition."""

    def __init__(self, direction: str, on_change: StateListener | None = None):
        self.direction = direction
        self.state = TurnState.LISTENING
        self.metrics = TurnMetrics()
        self._on_change = on_change
        self._lock = asyncio.Lock()
        self.history: list[tuple[float, TurnState]] = []

    async def to(self, state: TurnState) -> None:
        async with self._lock:
            if state == self.state:
                return
            self.state = state
            now = time.monotonic()
            self.history.append((now, state))
            if state == TurnState.PROCESSING:
                self.metrics = TurnMetrics(started_at=now, stt_final_at=now)
            elif state == TurnState.SPEAKING:
                self.metrics.audio_started_at = now
            elif state == TurnState.FALLBACK:
                self.metrics.fell_back = True
        if self._on_change:
            res = self._on_change(self.direction, state, self.metrics)
            if asyncio.iscoroutine(res):
                await res

    def mark_translated(self) -> None:
        self.metrics.translated_at = time.monotonic()

    @property
    def busy(self) -> bool:
        return self.state in (TurnState.PROCESSING, TurnState.SPEAKING, TurnState.FALLBACK)
