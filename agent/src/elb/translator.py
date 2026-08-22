"""The strict interpreter agent.

One instance per direction. It is NOT a conversational assistant: it never
answers, never asks, never advises, never adds. It renders what it heard into
the other language and stops.

Two guarantees are enforced in code, not left to the model:

  * LOCKED TERMS   - every critical term detected in the source must appear in
                     the output with its mandated rendering (glossary.enforce).
  * BOUNDED WAIT   - if STT or the LLM stall past the budget, the direction
                     drops to FALLBACK and speaks the untranslated source
                     rather than freezing the call (state.TurnState.FALLBACK).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterable

from livekit import rtc
from livekit.agents import Agent, ModelSettings, llm

from .bus import EV_METRIC, EV_TRANSCRIPT, EV_TRANSLATION, EventBus
from .glossary import Glossary
from .state import DirectionState, TurnState

logger = logging.getLogger("elb.translator")

LANG_NAMES = {
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
    "ja": "Japanese",
    "hi": "Hindi",
}

# Spoken marker prepended when we give up on translating and pass the original
# audio content through, so the listener knows why they are hearing gibberish.
FALLBACK_PREFIX = {
    "es": "Traduccion no disponible. Texto original:",
    "en": "Translation unavailable. Original:",
    "pt": "Traducao indisponivel. Original:",
    "fr": "Traduction indisponible. Original:",
    "de": "Ubersetzung nicht verfugbar. Original:",
    "it": "Traduzione non disponibile. Originale:",
}

INSTRUCTIONS = """\
You are a certified emergency-call interpreter rendering {src_name} into {dst_name}.

ABSOLUTE RULES - violating any of these endangers a life:
1. You TRANSLATE ONLY. You never answer, never ask questions, never give advice,
   never give medical or safety instructions, never comment, never greet, never
   apologise, never explain yourself.
2. Output ONLY the {dst_name} rendering of the input. No quotes, no labels, no
   preamble, no "Translation:", nothing else.
3. Preserve negation with absolute fidelity. "not breathing" must never become
   "breathing". If you are unsure whether a statement is negated, keep the
   negation.
4. Copy these through UNCHANGED, do not translate or localise them:
   proper names, street and place names, numbers, house/apartment numbers,
   phone numbers, ages, quantities, dosages, times, licence plates.
5. Keep the speaker's register and urgency. If they scream fragments, render
   fragments. Do not tidy their grammar, do not make them sound calm.
6. If the input is unintelligible, output exactly: [inaudible]
7. Never invent detail that was not said. Never resolve ambiguity by guessing.

LOCKED TERMS - if the source contains any phrase on the left, your output MUST
contain the exact uppercase string on the right, verbatim. Lines marked !! are
life-critical.
{locked}

Translate the next {src_name} utterance into {dst_name}. Output the translation
and nothing else."""


def build_instructions(src_lang: str, dst_lang: str, glossary: Glossary) -> str:
    return INSTRUCTIONS.format(
        src_name=LANG_NAMES.get(src_lang, src_lang),
        dst_name=LANG_NAMES.get(dst_lang, dst_lang),
        locked=glossary.locked_block(src_lang, dst_lang) or "(no glossary for this pair)",
    )


class TranslatorAgent(Agent):
    """Strict one-way interpreter for a single direction."""

    def __init__(
        self,
        *,
        direction: str,
        src_lang: str,
        dst_lang: str,
        speaker: str,
        glossary: Glossary,
        bus: EventBus,
        state: DirectionState,
        timeout_s: float,
        history_turns: int = 3,
        on_utterance=None,
        on_translation=None,
    ):
        super().__init__(instructions=build_instructions(src_lang, dst_lang, glossary))
        self.direction = direction
        self.src_lang = src_lang
        self.dst_lang = dst_lang
        self.speaker = speaker  # "caller" | "operator" - who produced the source
        self.glossary = glossary
        self.bus = bus
        self.state = state
        self.timeout_s = timeout_s
        self.history_turns = history_turns
        self._on_utterance = on_utterance
        self._on_translation = on_translation
        self._pending_source: str = ""
        self._seq = 0

    # ------------------------------------------------------------------ turn
    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        source = (new_message.text_content or "").strip()
        self._pending_source = source
        self._seq += 1

        if not source:
            return

        await self.state.to(TurnState.PROCESSING)

        hits = self.glossary.detect_for(source, self.src_lang, self.dst_lang)
        await self.bus.emit(
            EV_TRANSCRIPT,
            direction=self.direction,
            speaker=self.speaker,
            lang=self.src_lang,
            seq=self._seq,
            text=source,
            hits=[h.as_dict() for h in hits],
        )
        if self._on_utterance:
            self._on_utterance(self.speaker, self.src_lang, source, hits)

        # Keep the window short. A translator that remembers too much starts
        # "helping" - answering the previous question instead of translating
        # the current sentence.
        keep = self.history_turns * 2
        items = [it for it in turn_ctx.items if getattr(it, "role", None) != "system"]
        if len(items) > keep:
            trimmed = llm.ChatContext.empty()
            for it in items[-keep:]:
                trimmed.items.append(it)
            await self.update_chat_ctx(trimmed)

    # ------------------------------------------------------------------- llm
    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[str, None]:
        """Translate with a hard deadline, then enforce the glossary.

        We intentionally do NOT stream token-by-token into TTS: the glossary
        repair pass needs the complete sentence before audio starts. On a
        one-or-two sentence utterance with Haiku that costs a few hundred ms
        and buys a guarantee that a dropped "NOT" cannot reach the operator.
        """
        source = self._pending_source
        started = time.monotonic()

        async def _collect() -> str:
            parts: list[str] = []
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                if isinstance(chunk, str):
                    parts.append(chunk)
                else:
                    delta = getattr(chunk, "delta", None)
                    if delta is not None and getattr(delta, "content", None):
                        parts.append(delta.content)
            return "".join(parts).strip()

        translated = ""
        fell_back = False
        try:
            translated = await asyncio.wait_for(_collect(), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "%s: translation exceeded %.1fs, falling back to source passthrough",
                self.direction,
                self.timeout_s,
            )
            fell_back = True
        except Exception as exc:  # provider error, auth, quota...
            logger.error("%s: translation failed: %s", self.direction, exc)
            fell_back = True

        if not translated:
            fell_back = True

        repaired: list[str] = []
        contradicted: list[str] = []

        if fell_back:
            await self.state.to(TurnState.FALLBACK)
            prefix = FALLBACK_PREFIX.get(self.dst_lang, FALLBACK_PREFIX["en"])
            # Still surface the locked terms - they are the part that matters.
            locked = [
                h.fixed_target
                for h in self.glossary.detect_for(source, self.src_lang, self.dst_lang)
                if h.fixed_target
            ]
            tail = (" [" + "; ".join(dict.fromkeys(locked)) + "]") if locked else ""
            translated = f"{prefix} {source}{tail}" if source else prefix
        else:
            contradicted = self.glossary.contradicts(
                source, translated, self.src_lang, self.dst_lang
            )
            translated, repaired = self.glossary.enforce(
                source, translated, self.src_lang, self.dst_lang
            )
            if repaired or contradicted:
                logger.warning(
                    "%s: glossary repair applied repaired=%s contradicted=%s",
                    self.direction,
                    repaired,
                    contradicted,
                )

        self.state.mark_translated()
        self.state.metrics.repaired_terms = repaired

        elapsed_ms = int((time.monotonic() - started) * 1000)
        await self.bus.emit(
            EV_TRANSLATION,
            direction=self.direction,
            speaker=self.speaker,
            lang=self.dst_lang,
            seq=self._seq,
            source=source,
            text=translated,
            fallback=fell_back,
            repaired=repaired,
            contradicted=contradicted,
            latency_ms=elapsed_ms,
        )
        await self.bus.emit(
            EV_METRIC,
            direction=self.direction,
            metric="translate_ms",
            value=elapsed_ms,
            fallback=fell_back,
        )

        if self._on_translation:
            self._on_translation(self.speaker, self.dst_lang, source, translated, fell_back)

        yield translated

    # ------------------------------------------------------------------- tts
    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        first = True
        try:
            async for frame in Agent.default.tts_node(self, text, model_settings):
                if first:
                    first = False
                    await self.state.to(TurnState.SPEAKING)
                yield frame
        finally:
            await self.state.to(TurnState.LISTENING)
