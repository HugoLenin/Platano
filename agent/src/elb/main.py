"""Emergency Language Bridge - LiveKit agent worker.

Topology inside ONE process:

    room (LiveKit)
      |- caller            (Android, publishes mic)
      |- operator          (web console, publishes mic)
      |- agent connection A  -> AgentSession A: caller  -> operator
      |                        publishes track "interpreter-to-operator"
      |- agent connection B  -> AgentSession B: operator -> caller
                               publishes track "interpreter-to-caller"

Two connections rather than two tracks on one connection: each RoomIO owns one
audio output, and giving each direction its own participant makes the client
subscription rule trivial and impossible to get wrong. Both tracks are also
explicitly NAMED, so clients can filter on either identity or name.

Clients MUST connect with auto-subscribe disabled and subscribe only to their
own track - LiveKit subscribes to everything by default, which would make each
side hear the interpretation meant for the other.
"""

from __future__ import annotations

# Load agent/.env BEFORE importing .config, which snapshots os.environ the
# moment it is imported. Doing it here (the entrypoint) and not in the package
# __init__ keeps `import elb.config` credential-free for the test suite, and
# also gets LIVEKIT_URL into the environment where the livekit CLI worker
# reads it directly.
from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    room_io,
)
import anthropic as anthropic_sdk
from livekit.plugins import anthropic, deepgram, elevenlabs, silero

from .bus import EV_CALL, EV_NOTIFY, EV_STATE, EventBus
from .comprehension import ComprehensionEngine, Extraction, NotifyDecision
from .config import (
    DATA_TOPIC,
    IDENTITY_CALLER,
    IDENTITY_OPERATOR,
    TRACK_TO_CALLER,
    TRACK_TO_OPERATOR,
    settings,
)
from .glossary import load_glossary
from .notify import KIND_EARLY, KIND_FINAL, Notifier, NotifyPayload, NotifyTarget
from .report import ReportData, Scope, TranscriptLine, build_report, report_filename
from .signing import SCOPE_OPERATOR, link_for, mint
from .state import DirectionState, TurnState
from .store import Store
from .translator import TranslatorAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
)
logger = logging.getLogger("elb.main")

DIR_TO_OPERATOR = "to_operator"
DIR_TO_CALLER = "to_caller"


def prewarm(proc: JobProcess) -> None:
    """Load VAD once per worker process, not once per call."""
    proc.userdata["vad"] = silero.VAD.load()


async def _wait_for_participant(
    room: rtc.Room, identity: str, timeout: float
) -> rtc.RemoteParticipant | None:
    existing = room.remote_participants.get(identity)
    if existing:
        return existing
    fut: asyncio.Future[rtc.RemoteParticipant] = asyncio.get_running_loop().create_future()

    def _on_join(p: rtc.RemoteParticipant) -> None:
        if p.identity == identity and not fut.done():
            fut.set_result(p)

    room.on("participant_connected", _on_join)
    try:
        for p in room.remote_participants.values():
            if p.identity == identity:
                return p
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        try:
            room.off("participant_connected", _on_join)
        except Exception:
            pass


class Call:
    """Everything that belongs to one emergency call."""

    def __init__(self, ctx: JobContext):
        self.ctx = ctx
        self.call_id = str(uuid.uuid4())
        self.report_id = "elb_" + uuid.uuid4().hex[:16]
        self.started_at = datetime.now(timezone.utc)
        self.started_mono = time.monotonic()
        self.ended_at: datetime | None = None

        self.bus = EventBus()
        self.store = Store(settings)
        self.notifier = Notifier(settings)
        self.glossary = load_glossary(str(settings.glossary_path))

        self.caller_lang = "en"
        self.operator_lang = settings.operator_language
        self.user_id = ""
        self.caller_name = ""
        self.gps: tuple[float, float] | None = None

        self.transcript: list[TranscriptLine] = []
        self.latencies: list[int] = []
        self.fallback_turns = 0
        self.repaired_terms: list[str] = []
        self.critical_flags: set[str] = set()

        self.comprehension: ComprehensionEngine | None = None
        self.room_b: rtc.Room | None = None
        self.session_a: AgentSession | None = None
        self.session_b: AgentSession | None = None
        self._finalized = False
        self._pending_rows: list[dict] = []

    # ------------------------------------------------------------- utilities
    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_mono

    def _on_state(self, direction: str, state: TurnState, metrics) -> None:
        asyncio.create_task(
            self.bus.emit(
                EV_STATE,
                direction=direction,
                state=state.value,
                fallback=metrics.fell_back,
                translate_ms=metrics.translate_ms,
            )
        )
        if state is TurnState.LISTENING and metrics.translate_ms:
            self.latencies.append(metrics.translate_ms)
            if metrics.fell_back:
                self.fallback_turns += 1
            self.repaired_terms.extend(metrics.repaired_terms)

    def _record_utterance(self, speaker: str, lang: str, text: str, hits) -> None:
        ids = [h.term_id for h in hits]
        self.critical_flags.update(
            i for i in ids if self.glossary.terms[i].severity == "critical"
        )
        line = TranscriptLine(
            t_offset_s=self.elapsed,
            speaker=speaker,
            lang=lang,
            text=text,
            hits=ids,
        )
        self.transcript.append(line)
        self._pending_rows.append(
            {
                "call_id": self.call_id,
                "t_offset_ms": int(self.elapsed * 1000),
                "speaker": speaker,
                "lang": lang,
                "text": text,
                "hits": ids,
                "kind": "source",
            }
        )
        if self.comprehension:
            # The transcript line itself is handed to the extractor later, in
            # attach_translation, so it arrives with both languages at once.
            self.comprehension.note_critical(ids)

    def attach_translation(self, speaker: str, lang: str, source: str, text: str, fallback: bool) -> None:
        for line in reversed(self.transcript):
            if line.speaker == speaker and line.text == source and not line.rendered:
                line.rendered = text
                line.rendered_lang = lang
                line.fallback = fallback
                break
        if self.comprehension:
            # Give the extractor the bilingual pair: the operator-language
            # rendering is what its prompt is calibrated on, but the original
            # is what it must not lose.
            asyncio.create_task(
                self.comprehension.add_line(speaker, lang, source, rendered=text)
            )
        self._pending_rows.append(
            {
                "call_id": self.call_id,
                "t_offset_ms": int(self.elapsed * 1000),
                "speaker": speaker,
                "lang": lang,
                "text": text,
                "hits": [],
                "kind": "fallback" if fallback else "translation",
            }
        )

    async def flush_rows(self) -> None:
        rows, self._pending_rows = self._pending_rows, []
        await self.store.add_transcripts(rows)

    # --------------------------------------------------------------- reports
    async def build_reports(self, is_final: bool) -> dict[str, str]:
        extraction = self.comprehension.state.snapshot() if self.comprehension else {}
        data = ReportData(
            report_id=self.report_id,
            call_id=self.call_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            caller_lang=self.caller_lang,
            operator_lang=self.operator_lang,
            caller_name=self.caller_name,
            extraction=extraction,
            critical_flags=sorted(self.critical_flags),
            transcript=self.transcript,
            latencies_ms=self.latencies,
            fallback_turns=self.fallback_turns,
            repaired_terms=self.repaired_terms,
            gps=self.gps,
            is_final=is_final,
        )
        operator_txt = build_report(data, Scope.OPERATOR, self.operator_lang)
        family_txt = build_report(data, Scope.FAMILY, self.caller_lang)

        op_path = self.store.write_local_report(report_filename(data, Scope.OPERATOR), operator_txt)
        fam_path = self.store.write_local_report(report_filename(data, Scope.FAMILY), family_txt)
        logger.info("reports written: %s | %s", op_path.name, fam_path.name)

        await self.store.save_report(
            {
                "id": self.report_id,
                "call_id": self.call_id,
                "is_final": is_final,
                "operator_txt": operator_txt,
                "family_txt": family_txt,
                "extraction": extraction,
                "critical_flags": sorted(self.critical_flags),
                "lat": self.gps[0] if self.gps else None,
                "lon": self.gps[1] if self.gps else None,
                "caller_lang": self.caller_lang,
                "operator_lang": self.operator_lang,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {"operator": operator_txt, "family": family_txt}

    async def dispatch(self, kind: str, extraction: Extraction | None) -> None:
        contacts = await self.store.trusted_contacts(self.user_id)
        if not contacts:
            logger.info("no trusted contacts for user %r - nothing to notify", self.user_id or "?")
            await self.bus.emit(EV_NOTIFY, kind=kind, delivered=0, reason="no trusted contacts")
            return

        field = "notify_early" if kind == KIND_EARLY else "notify_final"
        targets = [
            NotifyTarget(
                contact_id=str(c["id"]),
                name=c.get("name") or "",
                phone_e164=c.get("phone_e164") or "",
                email=c.get("email") or "",
                locale=c.get("locale") or self.caller_lang,
                relationship=c.get("relationship") or "",
            )
            for c in contacts
            if c.get(field, True)
        ]
        if not targets:
            await self.bus.emit(EV_NOTIFY, kind=kind, delivered=0, reason="all contacts opted out")
            return

        e = extraction
        payload = NotifyPayload(
            call_id=self.call_id,
            report_id=self.report_id,
            kind=kind,
            caller_name=self.caller_name or "Un contacto",
            emergency_type=(e.emergency_type.value if e else ""),
            location=(e.location.value if e else ""),
            severity=(e.severity if e else ""),
            summary=(e.summary_operator if e else ""),
            lat=self.gps[0] if self.gps else None,
            lon=self.gps[1] if self.gps else None,
            targets=targets,
        )
        result = await self.notifier.send(payload)

        for cid, link in (result.get("links") or {}).items():
            await self.store.record_link(
                {
                    "report_id": self.report_id,
                    "contact_id": cid,
                    "scope": link["scope"],
                    "expires_at": datetime.fromtimestamp(
                        link["expires_at"], tz=timezone.utc
                    ).isoformat(),
                    "kind": kind,
                }
            )
            # The signed link IS the deliverable. With no delivery channel
            # configured it is the only way to reach the report, so it goes to
            # the log instead of nowhere. It is a capability URL: treat the log
            # like a credential store.
            logger.info("report link [%s] contact=%s -> %s", link["scope"], cid, link["url"])

        # Report what happened, not how many contacts existed. A deployment with
        # no delivery channel configured minted the links and sent nothing: that
        # is neither a success nor an error, and calling it either one teaches
        # the dispatcher to distrust the indicator.
        inner = result.get("result") if isinstance(result.get("result"), dict) else {}
        failures = int(inner.get("failures") or 0)
        await self.bus.emit(
            EV_NOTIFY,
            kind=kind,
            delivered=int(inner.get("delivered") or 0),
            prepared=len(targets),
            ok=bool(result.get("ok")) and failures == 0,
            reason=inner.get("note") or result.get("error"),
            detail=inner or result.get("error"),
        )

    async def on_early_notify(self, extraction: Extraction, decision: NotifyDecision) -> None:
        logger.info("EARLY NOTIFICATION triggered: %s", decision.reason)
        await self.build_reports(is_final=False)
        await self.dispatch(KIND_EARLY, extraction)

    async def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self.ended_at = datetime.now(timezone.utc)
        logger.info("finalizing call %s", self.call_id)

        await self.bus.emit(EV_CALL, phase="ending", call_id=self.call_id)

        if self.comprehension:
            # Last pass on the strong model - the call is over, latency no
            # longer matters, judgement does.
            try:
                await asyncio.wait_for(
                    self.comprehension.run_once(model=settings.report_model), timeout=60
                )
            except Exception as exc:
                logger.warning("final extraction pass failed: %s", exc)
            await self.comprehension.stop()

        await self.flush_rows()
        await self.build_reports(is_final=True)
        await self.store.update_call(
            self.call_id,
            {
                "ended_at": self.ended_at.isoformat(),
                "duration_s": int(self.elapsed),
                "fallback_turns": self.fallback_turns,
                "turns": len(self.latencies),
                "status": "closed",
            },
        )
        await self.dispatch(KIND_FINAL, self.comprehension.state.latest if self.comprehension else None)

        try:
            token, _ = mint(
                settings.report_signing_secret,
                report_id=self.report_id,
                contact_id="operator",
                scope=SCOPE_OPERATOR,
                ttl_s=settings.link_ttl_final_s,
            )
            await self.bus.emit(
                EV_CALL,
                phase="report_ready",
                call_id=self.call_id,
                report_id=self.report_id,
                operator_url=link_for(settings.web_base_url, token),
            )
        except ValueError as exc:
            logger.error("no signing secret, operator link not minted: %s", exc)

        await self.notifier.aclose()
        await self.store.aclose()


async def entrypoint(ctx: JobContext) -> None:
    missing = settings.missing_required()
    if missing:
        logger.error("missing required environment: %s", ", ".join(missing))
        return

    call = Call(ctx)
    await ctx.connect()
    call.bus.attach(ctx.room)
    logger.info("agent joined room %s as %s", ctx.room.name, ctx.room.local_participant.identity)

    # --- learn who is on the call -----------------------------------------
    caller = await _wait_for_participant(ctx.room, IDENTITY_CALLER, timeout=60.0)
    if caller is None:
        logger.error("caller never joined room %s - aborting", ctx.room.name)
        return

    attrs = dict(caller.attributes or {})
    call.caller_lang = (attrs.get("lang") or "en").split("-")[0].lower()
    call.user_id = attrs.get("user_id") or ""
    call.caller_name = attrs.get("display_name") or caller.name or ""
    if attrs.get("lat") and attrs.get("lon"):
        try:
            call.gps = (float(attrs["lat"]), float(attrs["lon"]))
        except ValueError:
            pass

    # The name normally rides in on the participant attributes. When the phone
    # was set up without one, fall back to the stored profile so the message a
    # relative receives says who is calling instead of "Un contacto". Wrapped in
    # the usual best-effort contract: no Supabase, no problem.
    if not call.caller_name and call.user_id:
        profile = await call.store.caller_profile(call.user_id)
        if profile:
            call.caller_name = profile.get("display_name") or ""
            if not attrs.get("lang") and profile.get("preferred_language"):
                call.caller_lang = str(profile["preferred_language"]).split("-")[0].lower()

    if not call.glossary.supports(call.caller_lang):
        logger.warning(
            "no glossary for %r - locked terms disabled for this call", call.caller_lang
        )

    logger.info(
        "caller lang=%s user=%s name=%r | operator lang=%s",
        call.caller_lang,
        call.user_id or "-",
        call.caller_name,
        call.operator_lang,
    )

    await call.store.create_call(
        {
            "id": call.call_id,
            "room": ctx.room.name,
            "user_id": call.user_id or None,
            "caller_lang": call.caller_lang,
            "operator_lang": call.operator_lang,
            "started_at": call.started_at.isoformat(),
            "status": "active",
        }
    )
    await call.bus.emit(
        EV_CALL,
        phase="started",
        call_id=call.call_id,
        report_id=call.report_id,
        caller_lang=call.caller_lang,
        operator_lang=call.operator_lang,
        caller_name=call.caller_name,
        glossary_version=call.glossary.version,
    )

    # --- comprehension (never blocks translation) --------------------------
    call.comprehension = ComprehensionEngine(
        settings, call.bus, call.operator_lang, caller_lang=call.caller_lang
    )
    call.comprehension.set_notify_callback(call.on_early_notify)
    call.comprehension.start()

    # --- second connection for the operator -> caller direction ------------
    room_b = rtc.Room()
    call.room_b = room_b
    token_b = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(TRACK_TO_CALLER)
        .with_name("Interpreter (to caller)")
        .with_attributes({"role": "interpreter", "direction": DIR_TO_CALLER})
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=ctx.room.name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
    vad = ctx.proc.userdata.get("vad") or silero.VAD.load()

    # GOTCHA (verified 2026-08, livekit-plugins-anthropic 1.7.0 + anthropic 1.0.0):
    # the plugin declares `anthropic>=0.41` with no upper bound and hands the
    # SDK an `httpx.AsyncClient`. anthropic 1.x moved to httpx2 and rejects it
    # with `TypeError: Invalid http_client argument`. The plugin does honour an
    # injected client, so we build it ourselves and hand it over. Remove this
    # once the plugin pins/updates. See docs/DECISIONS.md.
    claude = anthropic_sdk.AsyncAnthropic(api_key=settings.anthropic_api_key)

    def make_session(dst_lang: str, voice_id: str) -> AgentSession:
        return AgentSession(
            vad=vad,
            tts=elevenlabs.TTS(
                model=settings.tts_model,
                voice_id=voice_id,
                language=dst_lang,
                api_key=settings.elevenlabs_api_key,
            ),
            stt=deepgram.STT(
                model=settings.stt_model,
                # "multi" lets Nova-3 code-switch, which callers under stress do
                # constantly. Deepgram Nova-3 is used deliberately instead of
                # ElevenLabs Scribe v2 Realtime - see docs/DECISIONS.md.
                language="multi",
                api_key=settings.deepgram_api_key,
            ),
            llm=anthropic.LLM(
                model=settings.translate_model,
                client=claude,
                # NO temperature: the plugin forwards it straight into
                # messages.create(**extra), and the anthropic 1.x SDK removed
                # the parameter - every call died with
                #   TypeError: unexpected keyword argument 'temperature'
                # LiveKit retries that with backoff, so it did not surface as
                # an error: it burned the 3.5s budget and silently fell back to
                # source passthrough on EVERY turn. Determinism now comes from
                # the system prompt alone. Same family as the httpx2 gotcha in
                # docs/DECISIONS.md D9.
                # A translation is never longer than a few sentences; capping
                # this bounds the worst-case latency of a runaway generation.
                max_tokens=600,
            ),
            turn_handling={
                # Turn-based by design. No barge-in: it is out of scope and
                # would only muddy a two-direction interpretation demo.
                "interruption": {"enabled": False},
                "endpointing": {"min_delay": 0.4, "max_delay": 2.0},
                # Preemptive generation would translate a half-finished
                # sentence. Wrong translation beats slow translation only in
                # marketing, not in an emergency.
                "preemptive_generation": {"enabled": False},
            },
        )

    state_a = DirectionState(DIR_TO_OPERATOR, call._on_state)
    state_b = DirectionState(DIR_TO_CALLER, call._on_state)

    agent_a = TranslatorAgent(
        direction=DIR_TO_OPERATOR,
        src_lang=call.caller_lang,
        dst_lang=call.operator_lang,
        speaker="caller",
        glossary=call.glossary,
        bus=call.bus,
        state=state_a,
        timeout_s=settings.translate_timeout_s,
        on_utterance=call._record_utterance,
        on_translation=call.attach_translation,
    )
    agent_b = TranslatorAgent(
        direction=DIR_TO_CALLER,
        src_lang=call.operator_lang,
        dst_lang=call.caller_lang,
        speaker="operator",
        glossary=call.glossary,
        bus=call.bus,
        state=state_b,
        timeout_s=settings.translate_timeout_s,
        on_utterance=call._record_utterance,
        on_translation=call.attach_translation,
    )

    session_a = make_session(call.operator_lang, settings.tts_voice_operator)
    session_b = make_session(call.caller_lang, settings.tts_voice_caller)
    call.session_a, call.session_b = session_a, session_b

    # caller -> operator, spoken on the "interpreter-to-operator" track
    await session_a.start(
        agent=agent_a,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=IDENTITY_CALLER,
            audio_output=room_io.AudioOutputOptions(track_name=TRACK_TO_OPERATOR),
            text_input=False,
            text_output=False,
            close_on_disconnect=False,
        ),
    )
    # Connect the second room ONLY now. Everything above (Silero VAD, the two
    # turn-detector ONNX models, the plugin construction) is synchronous and
    # blocks the event loop. Connecting before it means the Rust FFI fires its
    # ConnectCallback and then waits for a ReadyForRoomEventRequest that the
    # blocked loop cannot send in time:
    #   FFI Panic: timed out waiting for ReadyForRoomEventRequest
    # which kills the whole worker process mid-call. Observed live.
    await room_b.connect(settings.livekit_url, token_b)
    logger.info("second interpreter connection joined as %s", TRACK_TO_CALLER)

    # operator -> caller, spoken on the "interpreter-to-caller" track
    await session_b.start(
        agent=agent_b,
        room=room_b,
        room_options=room_io.RoomOptions(
            participant_identity=IDENTITY_OPERATOR,
            audio_output=room_io.AudioOutputOptions(track_name=TRACK_TO_CALLER),
            text_input=False,
            text_output=False,
            close_on_disconnect=False,
        ),
    )

    logger.info("both interpretation directions are live")
    await call.bus.emit(EV_CALL, phase="ready", call_id=call.call_id)

    # --- client -> agent control channel -----------------------------------
    def _on_data(packet: rtc.DataPacket) -> None:
        if packet.topic and packet.topic != DATA_TOPIC:
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        kind = msg.get("type")
        if kind == "location":
            try:
                call.gps = (float(msg["lat"]), float(msg["lon"]))
                logger.info("caller location updated: %.5f, %.5f", *call.gps)
            except (KeyError, TypeError, ValueError):
                pass
        elif kind == "hello" and msg.get("role") == "operator":
            lang = (msg.get("lang") or "").split("-")[0].lower()
            if lang:
                call.operator_lang = lang
            asyncio.create_task(call.bus.replay_to(ctx.room, packet.participant.identity))
        elif kind == "end_call":
            logger.info("end_call requested by %s", getattr(packet.participant, "identity", "?"))
            asyncio.create_task(call.finalize())

    ctx.room.on("data_received", _on_data)

    def _on_left(p: rtc.RemoteParticipant) -> None:
        if p.identity in (IDENTITY_CALLER, IDENTITY_OPERATOR):
            logger.info("%s disconnected - closing call", p.identity)
            asyncio.create_task(call.finalize())

    ctx.room.on("participant_disconnected", _on_left)

    async def _flusher() -> None:
        while not call._finalized:
            await asyncio.sleep(5)
            await call.flush_rows()

    flusher = asyncio.create_task(_flusher())

    async def _shutdown() -> None:
        flusher.cancel()
        await call.finalize()
        try:
            await room_b.disconnect()
        except Exception:
            pass

    ctx.add_shutdown_callback(_shutdown)


def main() -> None:
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="elb-interpreter",
        )
    )


if __name__ == "__main__":
    main()
