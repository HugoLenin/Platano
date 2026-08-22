"""End-to-end call harness - no phone, no browser, no human.

Simulates a real emergency call against a running stack and verifies the audio
that comes out the other side:

    1. Synthesises the caller's speech with ElevenLabs (raw PCM, no decoder).
    2. Joins the LiveKit room twice: as `caller` (publishes that audio) and as
       `operator` (subscribes ONLY to the "interpreter-to-operator" track).
    3. Records what the operator hears and sends it to Deepgram.
    4. Prints the round-trip: what was said in, what came out, and the latency
       from end-of-speech to first interpreted audio.

It also proves the property that is easiest to break: the operator never
subscribes to the caller's interpretation track, so neither side hears the
audio meant for the other.

Prerequisites (see RUNBOOK.md):
    web dev server on :3000  ·  agent worker running  ·  agent/.env filled in

Usage:
    cd agent && PYTHONPATH=src py -3.13 ../scripts/e2e_call.py
    ... optionally:  --room elb-e2e --lang en --text "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
import wave
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / "agent" / ".env")

SR, CH = 16000, 1
FRAME = 160  # 10 ms at 16 kHz
OUT_SR = 48000  # what LiveKit delivers back
TRACK_TO_OPERATOR = "interpreter-to-operator"
TRACK_TO_CALLER = "interpreter-to-caller"

DEFAULT_TEXT = (
    "Help! There has been a car accident. My son is trapped in the back seat "
    "and he is not breathing. We are on Seventh Avenue with Forty Fifth street."
)


def _post(url: str, payload: dict, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})}
    )
    return urllib.request.urlopen(req, timeout=60).read()


def mint_token(base: str, role: str, room: str, lang: str) -> dict:
    return json.loads(
        _post(
            f"{base}/api/token",
            {
                "role": role,
                "room": room,
                "lang": lang,
                "display_name": "Amara Okafor" if role == "caller" else "Despachador",
                "user_id": "11111111-1111-1111-1111-111111111111",
            },
        )
    )


def synthesize(text: str) -> bytes:
    """Caller audio as raw PCM. `pcm_16000` avoids needing an mp3 decoder."""
    voice = os.getenv("ELB_TTS_VOICE_CALLER", "ODq5zmih8GrVes37Dizd")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_{SR}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text, "model_id": os.getenv("ELB_TTS_MODEL", "eleven_flash_v2_5")}).encode(),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "content-type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=120).read()


def transcribe(wav_path: Path) -> str:
    url = "https://api.deepgram.com/v1/listen?model=nova-3&language=multi&smart_format=true&punctuate=true"
    req = urllib.request.Request(
        url,
        data=wav_path.read_bytes(),
        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "audio/wav"},
    )
    body = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return body["results"]["channels"][0]["alternatives"][0]["transcript"]


def peak(pcm: bytes) -> int:
    return max(
        (abs(int.from_bytes(pcm[i : i + 2], "little", signed=True)) for i in range(0, len(pcm) - 1, 2)),
        default=0,
    )


async def run(args: argparse.Namespace) -> int:
    pcm = synthesize(args.text)
    print(f"  caller audio: {len(pcm) / 2 / SR:.1f}s of {SR} Hz PCM")

    heard = bytearray()
    first_voice_at: list[float | None] = [None]
    # Only start looking for interpreted speech once the caller has stopped:
    # the track carries Opus comfort noise before that, which would otherwise
    # register as "the interpreter is talking" and give a negative latency.
    armed_at: list[float | None] = [None]
    crosstalk = [False]

    # ---- operator: subscribes to its own track and nothing else ------------
    op = rtc.Room()
    op_creds = mint_token(args.base, "operator", args.room, "es")

    @op.on("track_published")
    def _published(pub, participant):  # noqa: ANN001
        if pub.name == TRACK_TO_OPERATOR:
            pub.set_subscribed(True)
            print(f"  [operator] subscribed to {pub.name}")
        elif pub.name == TRACK_TO_CALLER:
            print(f"  [operator] ignoring {pub.name} (belongs to the caller)")

    @op.on("track_subscribed")
    def _subscribed(track, pub, participant):  # noqa: ANN001
        if pub.name != TRACK_TO_OPERATOR:
            crosstalk[0] = True
            return

        async def pump() -> None:
            async for ev in rtc.AudioStream(track):
                data = bytes(ev.frame.data)
                now = time.time()
                if (
                    first_voice_at[0] is None
                    and armed_at[0] is not None
                    and now > armed_at[0]
                    and peak(data) > 1500
                ):
                    first_voice_at[0] = now
                heard.extend(data)

        asyncio.create_task(pump())

    await op.connect(op_creds["url"], op_creds["token"], rtc.RoomOptions(auto_subscribe=False))
    print(f"  operator joined '{args.room}'")

    # ---- caller: publishes the emergency ------------------------------------
    caller = rtc.Room()
    cl_creds = mint_token(args.base, "caller", args.room, args.lang)
    await caller.connect(cl_creds["url"], cl_creds["token"], rtc.RoomOptions(auto_subscribe=False))
    source = rtc.AudioSource(SR, CH)
    await caller.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("mic", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    print("  caller joined and publishing")

    silence = b"\x00" * (FRAME * 2)
    for _ in range(30):  # 300 ms of room tone so VAD settles
        await source.capture_frame(rtc.AudioFrame(silence, SR, CH, FRAME))
        await asyncio.sleep(0.01)

    for i in range(0, len(pcm) - FRAME * 2, FRAME * 2):  # real time, not faster
        await source.capture_frame(rtc.AudioFrame(pcm[i : i + FRAME * 2], SR, CH, FRAME))
        await asyncio.sleep(0.01)
    spoke_until = time.time()
    armed_at[0] = spoke_until
    print("  caller stopped talking, waiting for the interpretation...")

    for _ in range(args.wait * 100):  # silence is what triggers end-of-turn
        await source.capture_frame(rtc.AudioFrame(silence, SR, CH, FRAME))
        await asyncio.sleep(0.01)
        if first_voice_at[0]:
            break
    await asyncio.sleep(args.tail)

    out = Path(args.out)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_SR)
        w.writeframes(bytes(heard))

    await caller.disconnect()
    await op.disconnect()

    print()
    print(f"  said (caller, {args.lang}): {args.text}")
    if not heard:
        print("  FAIL: the operator received no audio at all")
        return 1
    text = transcribe(out)
    print(f"  heard (operator, es): {text or '(silence)'}")
    if first_voice_at[0]:
        print(f"  latency end-of-speech -> first interpreted audio: {first_voice_at[0] - spoke_until:.2f}s")
    print(f"  crosstalk (operator hearing the caller's track): {'YES - BUG' if crosstalk[0] else 'no'}")
    return 0 if text.strip() and not crosstalk[0] else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--room", default="elb-e2e")
    p.add_argument("--lang", default="en", help="caller language")
    p.add_argument("--text", default=DEFAULT_TEXT)
    p.add_argument("--base", default=os.getenv("ELB_WEB_BASE_URL", "http://localhost:3000"))
    p.add_argument("--wait", type=int, default=30, help="seconds to wait for the first interpreted audio")
    p.add_argument("--tail", type=int, default=14, help="seconds to keep recording after it starts")
    p.add_argument("--out", default=str(REPO / "reports" / "e2e_heard.wav"))
    args = p.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
