"use client";

/**
 * LiveKit connection for the operator console.
 *
 * The important bit is `autoSubscribe: false`. LiveKit subscribes a client to
 * every track in the room by default, which here would mean the operator hears
 * BOTH interpreter outputs at once - including the Spanish->caller-language
 * rendering meant for the other end. We therefore subscribe to exactly one
 * track, matched by name.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ConnectionState,
  RemoteAudioTrack,
  RemoteTrack,
  RemoteTrackPublication,
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import {
  DATA_TOPIC,
  decodeEvent,
  encodeMessage,
  type ElbEvent,
  type Extraction,
  type TurnState,
  TRACK_TO_OPERATOR,
} from "@/lib/events";

export interface TranscriptEntry {
  key: string;
  seq: number;
  speaker: "caller" | "operator";
  lang: string;
  text: string;
  rendered?: string;
  renderedLang?: string;
  fallback?: boolean;
  repaired?: string[];
  latencyMs?: number;
  ts: number;
}

export interface CallInfo {
  callId?: string;
  reportId?: string;
  callerLang?: string;
  operatorLang?: string;
  callerName?: string;
  phase?: string;
  operatorUrl?: string;
  glossaryVersion?: string;
}

export interface NotifyLog {
  kind: string;
  delivered: number;
  ok?: boolean;
  reason?: string;
  ts: number;
}

export function useElbRoom(role: "operator" | "caller" = "operator") {
  const roomRef = useRef<Room | null>(null);
  const audioRef = useRef<HTMLDivElement | null>(null);

  const [state, setState] = useState<ConnectionState>(ConnectionState.Disconnected);
  const [error, setError] = useState<string>("");
  const [roomName, setRoomName] = useState<string>("");
  const [micOn, setMicOn] = useState(false);
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [criticalFlags, setCriticalFlags] = useState<string[]>([]);
  const [notifyState, setNotifyState] = useState<{ armed: boolean; reason: string }>({
    armed: false,
    reason: "esperando datos suficientes",
  });
  const [notifications, setNotifications] = useState<NotifyLog[]>([]);
  const [dirState, setDirState] = useState<Record<string, TurnState>>({
    to_operator: "listening",
    to_caller: "listening",
  });
  const [latencies, setLatencies] = useState<number[]>([]);
  const [call, setCall] = useState<CallInfo>({});
  const [agentPresent, setAgentPresent] = useState(false);

  const wantTrack = role === "operator" ? TRACK_TO_OPERATOR : "interpreter-to-caller";

  const handleEvent = useCallback((ev: ElbEvent) => {
    switch (ev.type) {
      case "transcript":
        setEntries((prev) => [
          ...prev,
          {
            key: `${ev.speaker}-${ev.seq}-${ev.ts}`,
            seq: ev.seq,
            speaker: ev.speaker,
            lang: ev.lang,
            text: ev.text,
            ts: ev.ts,
          },
        ]);
        break;

      case "translation":
        setEntries((prev) => {
          const idx = [...prev]
            .reverse()
            .findIndex((e) => e.speaker === ev.speaker && e.text === ev.source && !e.rendered);
          if (idx === -1) {
            return [
              ...prev,
              {
                key: `${ev.speaker}-${ev.seq}-${ev.ts}-t`,
                seq: ev.seq,
                speaker: ev.speaker,
                lang: ev.lang,
                text: ev.source,
                rendered: ev.text,
                renderedLang: ev.lang,
                fallback: ev.fallback,
                repaired: ev.repaired,
                latencyMs: ev.latency_ms,
                ts: ev.ts,
              },
            ];
          }
          const real = prev.length - 1 - idx;
          const next = [...prev];
          next[real] = {
            ...next[real],
            rendered: ev.text,
            renderedLang: ev.lang,
            fallback: ev.fallback,
            repaired: ev.repaired,
            latencyMs: ev.latency_ms,
          };
          return next;
        });
        if (ev.latency_ms) setLatencies((p) => [...p.slice(-49), ev.latency_ms]);
        break;

      case "state":
        setDirState((p) => ({ ...p, [ev.direction]: ev.state }));
        break;

      case "extraction":
        setExtraction(ev.data);
        setCriticalFlags(ev.critical_flags ?? []);
        setNotifyState({ armed: ev.notify, reason: ev.notify_reason });
        break;

      case "notify":
        setNotifications((p) => [
          ...p,
          { kind: ev.kind, delivered: ev.delivered, ok: ev.ok, reason: ev.reason, ts: ev.ts },
        ]);
        break;

      case "call":
        setCall((p) => ({
          ...p,
          phase: ev.phase,
          callId: ev.call_id ?? p.callId,
          reportId: ev.report_id ?? p.reportId,
          callerLang: ev.caller_lang ?? p.callerLang,
          operatorLang: ev.operator_lang ?? p.operatorLang,
          callerName: ev.caller_name ?? p.callerName,
          operatorUrl: ev.operator_url ?? p.operatorUrl,
          glossaryVersion: ev.glossary_version ?? p.glossaryVersion,
        }));
        break;
    }
  }, []);

  const connect = useCallback(
    async (opts: { room?: string; lang: string; displayName?: string }) => {
      setError("");
      try {
        const res = await fetch("/api/token", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            role,
            room: opts.room,
            lang: opts.lang,
            display_name: opts.displayName,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "no se pudo obtener el token");

        const room = new Room({ adaptiveStream: false, dynacast: false });
        roomRef.current = room;

        const maybeSubscribe = (pub: RemoteTrackPublication) => {
          // Subscribe to OUR interpretation track only.
          if (pub.kind === Track.Kind.Audio && pub.trackName === wantTrack) {
            pub.setSubscribed(true);
          }
        };

        room
          .on(RoomEvent.ConnectionStateChanged, setState)
          .on(RoomEvent.TrackPublished, (pub) => maybeSubscribe(pub))
          .on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
            if (track.kind === Track.Kind.Audio && audioRef.current) {
              const el = (track as RemoteAudioTrack).attach();
              el.autoplay = true;
              audioRef.current.appendChild(el);
            }
          })
          .on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => track.detach().forEach((e) => e.remove()))
          .on(RoomEvent.ParticipantConnected, (p) => {
            if (p.identity.startsWith("interpreter") || p.identity.startsWith("agent")) {
              setAgentPresent(true);
            }
          })
          .on(RoomEvent.DataReceived, (payload: Uint8Array, _p, _k, topic?: string) => {
            if (topic && topic !== DATA_TOPIC) return;
            const ev = decodeEvent(payload);
            if (ev) handleEvent(ev);
          })
          .on(RoomEvent.Disconnected, () => setState(ConnectionState.Disconnected));

        await room.connect(data.url, data.token, { autoSubscribe: false });
        setRoomName(data.room);

        // Anything published before we attached the listener.
        room.remoteParticipants.forEach((p) => {
          p.trackPublications.forEach((pub) => maybeSubscribe(pub as RemoteTrackPublication));
          if (p.identity.startsWith("interpreter") || p.identity.startsWith("agent")) {
            setAgentPresent(true);
          }
        });

        await room.localParticipant.setMicrophoneEnabled(true);
        setMicOn(true);

        // Tell the agent who we are and ask it to replay anything we missed.
        await room.localParticipant.publishData(
          encodeMessage({ type: "hello", role, lang: opts.lang }),
          { reliable: true, topic: DATA_TOPIC },
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setState(ConnectionState.Disconnected);
      }
    },
    [handleEvent, role, wantTrack],
  );

  const toggleMic = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const next = !micOn;
    await room.localParticipant.setMicrophoneEnabled(next);
    setMicOn(next);
  }, [micOn]);

  const endCall = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.localParticipant.publishData(encodeMessage({ type: "end_call" }), {
        reliable: true,
        topic: DATA_TOPIC,
      });
      // Give the agent a beat to start finalising before we drop the room.
      await new Promise((r) => setTimeout(r, 900));
    } finally {
      await room.disconnect();
      setState(ConnectionState.Disconnected);
    }
  }, []);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect().catch(() => undefined);
    };
  }, []);

  const medianLatency =
    latencies.length === 0
      ? 0
      : [...latencies].sort((a, b) => a - b)[Math.floor(latencies.length / 2)];

  return {
    audioRef,
    state,
    error,
    roomName,
    micOn,
    entries,
    extraction,
    criticalFlags,
    notifyState,
    notifications,
    dirState,
    medianLatency,
    call,
    agentPresent,
    connect,
    toggleMic,
    endCall,
    connected: state === ConnectionState.Connected,
  };
}
