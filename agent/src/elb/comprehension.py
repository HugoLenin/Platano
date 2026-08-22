"""Emergency comprehension: structured understanding, running beside the call.

Hard constraint: this must NEVER block or slow translation. It runs as a
detached task on a timer, reads a snapshot of the transcript, and every failure
mode is "log it and try again next tick".

It also owns the early-notification decision, because that decision is a
function of extraction confidence and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .bus import EV_EXTRACTION, EventBus
from .config import Settings

logger = logging.getLogger("elb.comprehension")


# --------------------------------------------------------------------------- schema
class Assessed(BaseModel):
    """A single extracted field with its own confidence and source evidence."""

    value: str = Field(description="Extracted value, or empty string if not stated.")
    confidence: float = Field(description="0.0-1.0. Use 0.0 when the field was never stated.")
    evidence: str = Field(description="Short verbatim quote from the transcript, or empty.")


class Victim(BaseModel):
    who: str = Field(description="How the caller referred to them, e.g. 'my son', 'the driver'.")
    condition: str = Field(description="Stated condition only. Never inferred, never diagnosed.")
    is_child: bool = Field(description="True only if the caller indicated a minor.")
    conscious: str = Field(description="'yes', 'no' or 'unknown' - only if explicitly stated.")


class Extraction(BaseModel):
    emergency_type: Assessed = Field(
        description="One of: medical, fire, police, traffic, rescue, other."
    )
    emergency_detail: Assessed = Field(description="Short specific description of what happened.")
    location: Assessed = Field(description="Street address or place as literally stated.")
    location_detail: Assessed = Field(description="Floor, apartment, landmark, access notes.")
    victim_count: Assessed = Field(description="Number of people affected, as stated.")
    victims: list[Victim] = Field(description="One entry per person mentioned. May be empty.")
    hazards: list[str] = Field(
        description="Active dangers for responders: fire, weapon, gas, traffic, collapse, animal."
    )
    caller_relationship: Assessed = Field(
        description="Caller's relation to the victims, e.g. 'mother', 'bystander'."
    )
    severity: str = Field(description="One of: critical, high, moderate, low.")
    summary_operator: str = Field(
        description=(
            "Two sentences maximum, in the OPERATOR's language, factual dispatch style. "
            "State only what was said. No advice, no instructions, no speculation."
        )
    )
    summary_family: str = Field(
        description=(
            "Two sentences maximum, in the CALLER's language, for a worried relative "
            "who is NOT a medical professional. Say what kind of emergency it is, "
            "where, and that emergency services have the details. "
            "STRICTLY FORBIDDEN: clinical descriptions, injuries, vital signs, "
            "diagnoses, prognosis, and any procedure or instruction the relative "
            "could attempt. Never tell them what to do beyond going to the location "
            "or waiting for official contact."
        )
    )


SYSTEM = """\
You extract structured dispatch data from a live emergency-call transcript.

You are a passive observer. You do not talk to anyone. You do not give advice.
You NEVER produce medical, rescue or safety instructions of any kind - not for
the caller, not for the operator, not for anybody. You only report what was said.

Rules:
- Extract only what is explicitly stated. Never infer, never fill gaps, never
  diagnose. If something was not said, use an empty string and confidence 0.0.
- Confidence is your calibrated belief that this field is correct AND complete:
    0.9-1.0 stated plainly and unambiguously, and confirmed or repeated
    0.7-0.89 stated once, clearly, no contradiction
    0.4-0.69 partial, hedged, or you had to interpret
    0.1-0.39 a guess from weak signal
    0.0      never stated
- A partial address ("a street in Chapinero") is at most 0.4. A full address
  with number is 0.8+. An address the caller corrected is the CORRECTED one.
- Preserve negation exactly. "not breathing" is a condition of "not breathing".
- The operator's language is {operator_lang}; write summary_operator in it.
- The caller's language is {caller_lang}; write summary_family in it.
- summary_family goes to a frightened relative, not a clinician. It must contain
  no clinical detail and no instruction of any kind. It informs, it never
  instructs. Getting this wrong is a product-level safety failure.
- Transcript lines are tagged [caller] and [operator], with the caller's words
  given in their original language followed by the interpreted rendering.
"""


# --------------------------------------------------------------------------- state
@dataclass
class NotifyDecision:
    should_notify: bool
    reason: str
    fast_path: bool = False


@dataclass
class ComprehensionState:
    latest: Extraction | None = None
    passes: int = 0
    started_at: float = field(default_factory=time.monotonic)
    early_sent: bool = False
    _stable_key: str | None = None
    _stable_count: int = 0
    critical_flags: set[str] = field(default_factory=set)

    def snapshot(self) -> dict[str, Any]:
        if not self.latest:
            return {}
        return json.loads(self.latest.model_dump_json())


CRITICAL_FAST_PATH = {"not_breathing", "no_pulse", "gun", "fire", "trapped", "unconscious"}


class ComprehensionEngine:
    def __init__(
        self, settings: Settings, bus: EventBus, operator_lang: str, caller_lang: str = "en"
    ):
        self.s = settings
        self.bus = bus
        self.operator_lang = operator_lang
        self.caller_lang = caller_lang
        self.state = ComprehensionState()
        self._client = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._transcript: list[str] = []
        self._lock = asyncio.Lock()
        self._on_notify = None

    def set_notify_callback(self, cb) -> None:
        self._on_notify = cb

    async def add_line(self, speaker: str, lang: str, text: str, rendered: str = "") -> None:
        line = f"[{speaker}:{lang}] {text}"
        if rendered and rendered != text:
            line += f"\n    -> {rendered}"
        async with self._lock:
            self._transcript.append(line)

    def note_critical(self, term_ids: list[str]) -> None:
        self.state.critical_flags.update(term_ids)

    @property
    def transcript_text(self) -> str:
        return "\n".join(self._transcript)

    # ----------------------------------------------------------------- runtime
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="elb-comprehension")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.s.extract_interval_s)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let comprehension kill the call
                logger.warning("extraction pass failed (continuing): %s", exc)

    def _client_or_none(self):
        if self._client is not None:
            return self._client
        if not self.s.anthropic_api_key:
            return None
        try:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self.s.anthropic_api_key)
        except Exception as exc:  # pragma: no cover
            logger.error("anthropic client unavailable: %s", exc)
            return None
        return self._client

    async def run_once(self, model: str | None = None) -> Extraction | None:
        async with self._lock:
            transcript = "\n".join(self._transcript)
        if len(transcript.strip()) < 20:
            return None

        client = self._client_or_none()
        if client is None:
            return None

        system = SYSTEM.format(
            operator_lang=self.operator_lang, caller_lang=self.caller_lang
        )
        user = f"TRANSCRIPT SO FAR:\n{transcript}\n\nExtract the dispatch record."

        extraction: Extraction | None = None
        try:
            resp = await client.messages.parse(
                model=model or self.s.extract_model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=Extraction,
            )
            extraction = resp.parsed_output
        except (AttributeError, TypeError):
            # Older SDK without messages.parse - fall back to a raw schema call.
            resp = await client.messages.create(
                model=model or self.s.extract_model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": Extraction.model_json_schema(),
                    }
                },
            )
            text = next(b.text for b in resp.content if b.type == "text")
            extraction = Extraction.model_validate_json(text)

        if extraction is None:
            return None

        self.state.latest = extraction
        self.state.passes += 1

        decision = self._evaluate(extraction)
        await self.bus.emit(
            EV_EXTRACTION,
            pass_no=self.state.passes,
            data=json.loads(extraction.model_dump_json()),
            critical_flags=sorted(self.state.critical_flags),
            notify=decision.should_notify,
            notify_reason=decision.reason,
        )

        if decision.should_notify and not self.state.early_sent:
            self.state.early_sent = True
            if self._on_notify:
                asyncio.create_task(self._on_notify(extraction, decision))
        return extraction

    # ------------------------------------------------------- notification gate
    def _evaluate(self, e: Extraction) -> NotifyDecision:
        """Decide whether the support network should be pinged now.

        Two gates, because the cost of a false positive (a relative is alarmed
        a bit early) is not symmetric with the cost of a false negative (they
        find out hours later).
        """
        if self.state.early_sent:
            return NotifyDecision(False, "already sent")

        loc_ok = bool(e.location.value.strip())
        type_ok = bool(e.emergency_type.value.strip())
        if not (loc_ok and type_ok):
            return NotifyDecision(False, "location or type still unknown")

        key = f"{e.emergency_type.value.strip().lower()}|{e.location.value.strip().lower()}"
        if key == self.state._stable_key:
            self.state._stable_count += 1
        else:
            self.state._stable_key = key
            self.state._stable_count = 1

        fast = bool(self.state.critical_flags & CRITICAL_FAST_PATH)
        min_loc = 0.60 if fast else self.s.notify_min_location_conf
        min_type = 0.60 if fast else self.s.notify_min_type_conf
        need_stable = 1 if fast else self.s.notify_stable_passes
        min_elapsed = 0.0 if fast else self.s.notify_min_elapsed_s

        elapsed = time.monotonic() - self.state.started_at

        if e.location.confidence < min_loc:
            return NotifyDecision(False, f"location confidence {e.location.confidence:.2f} < {min_loc}")
        if e.emergency_type.confidence < min_type:
            return NotifyDecision(
                False, f"type confidence {e.emergency_type.confidence:.2f} < {min_type}"
            )
        if self.state._stable_count < need_stable:
            return NotifyDecision(
                False, f"stable for {self.state._stable_count}/{need_stable} passes"
            )
        if elapsed < min_elapsed:
            return NotifyDecision(False, f"only {elapsed:.0f}s elapsed, need {min_elapsed:.0f}s")

        reason = (
            f"loc={e.location.confidence:.2f} type={e.emergency_type.confidence:.2f} "
            f"stable={self.state._stable_count}"
        )
        return NotifyDecision(True, ("fast-path: " if fast else "") + reason, fast_path=fast)
