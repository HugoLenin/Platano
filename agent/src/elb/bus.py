"""Realtime event bus over the LiveKit data channel.

Every UI surface (operator console, Android app) renders from these events.
Supabase is storage only - it is never in the signalling path, per the
architecture constraint.

Wire format: one JSON object per data packet, topic "elb".
    {"v":1, "type":"...", "ts":<epoch ms>, ...payload}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .config import DATA_TOPIC

if TYPE_CHECKING:  # rtc is used for typing only - keeps offline tests dependency-free
    from livekit import rtc

logger = logging.getLogger("elb.bus")

# Event types (also mirrored in web/lib/events.ts and the Android EventBus)
EV_TRANSCRIPT = "transcript"          # a finalized source utterance
EV_TRANSLATION = "translation"        # its rendering in the other language
EV_STATE = "state"                    # per-direction FSM transition
EV_EXTRACTION = "extraction"          # structured emergency understanding
EV_NOTIFY = "notify"                  # a support-network delivery happened
EV_CALL = "call"                      # call lifecycle
EV_METRIC = "metric"                  # latency sample


class EventBus:
    """Fan-out to the room, with an in-memory tail for late joiners."""

    def __init__(self, tail: int = 200):
        self._rooms: list["rtc.Room"] = []
        self._tail_max = tail
        self.tail: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def attach(self, room: "rtc.Room") -> None:
        if room not in self._rooms:
            self._rooms.append(room)

    async def emit(self, type_: str, **payload: Any) -> dict[str, Any]:
        event = {"v": 1, "type": type_, "ts": int(time.time() * 1000), **payload}
        async with self._lock:
            self.tail.append(event)
            if len(self.tail) > self._tail_max:
                self.tail = self.tail[-self._tail_max :]
        data = json.dumps(event, ensure_ascii=False).encode("utf-8")
        for room in list(self._rooms):
            try:
                if room.isconnected():
                    await room.local_participant.publish_data(
                        data, reliable=True, topic=DATA_TOPIC
                    )
                    # One publisher is enough; both connections share a room.
                    break
            except Exception as exc:  # pragma: no cover - transport hiccup
                logger.warning("publish_data failed: %s", exc)
        return event

    async def replay_to(self, room: "rtc.Room", identity: str) -> None:
        """Send the buffered tail to a participant that joined mid-call."""
        for event in list(self.tail):
            try:
                await room.local_participant.publish_data(
                    json.dumps(event, ensure_ascii=False).encode("utf-8"),
                    reliable=True,
                    topic=DATA_TOPIC,
                    destination_identities=[identity],
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("replay failed: %s", exc)
                return
