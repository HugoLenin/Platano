"use client";

/**
 * Dispatcher console.
 *
 * Layout follows what a 123/911 operator actually does, in priority order:
 *   left   - what is being said, right now, with critical terms impossible to miss
 *   right  - the dispatch card they would otherwise be typing by hand
 *   footer - interpretation health, so they know whether to trust it
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useElbRoom, type TranscriptEntry } from "@/components/useElbRoom";
import { fixedFor, highlight, GLOSSARY_VERSION } from "@/lib/glossary";
import type { Assessed, TurnState } from "@/lib/events";

const DEFAULT_ROOM = "elb-demo";

const OPERATOR_LANGS = [
  { code: "es", label: "Español" },
  { code: "en", label: "English" },
  { code: "pt", label: "Português" },
  { code: "fr", label: "Français" },
];

function Highlighted({ text, lang }: { text: string; lang: string }) {
  const segments = useMemo(() => highlight(text, lang), [text, lang]);
  return (
    <>
      {segments.map((s, i) =>
        s.termId ? (
          <mark
            key={i}
            className={`term-${s.severity ?? "info"}`}
            title={`${s.termId.replace(/_/g, " ")} → ${fixedFor(s.termId, "es")}`}
          >
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

function StateDot({ state }: { state: TurnState }) {
  const map: Record<TurnState, [string, string]> = {
    listening: ["bg-ink-600", "Escuchando"],
    processing: ["bg-warn-400 pulse", "Traduciendo"],
    speaking: ["bg-ok-400 pulse", "Reproduciendo"],
    fallback: ["bg-crit-500 pulse", "Respaldo"],
    error: ["bg-crit-600", "Error"],
  };
  const [cls, label] = map[state] ?? map.listening;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${cls}`} />
      <span className="text-[11px] text-ink-400">{label}</span>
    </span>
  );
}

function Field({ label, node, lang }: { label: string; node?: Assessed; lang: string }) {
  const value = node?.value?.trim();
  const conf = node?.confidence ?? 0;
  const tone = conf >= 0.75 ? "bg-ok-400" : conf >= 0.5 ? "bg-warn-400" : "bg-crit-500";
  return (
    <div className="border-b border-ink-800 py-2 last:border-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
          {label}
        </span>
        {value ? (
          <span className="flex items-center gap-1.5" title={`Confianza ${Math.round(conf * 100)}%`}>
            <span className="h-1 w-10 overflow-hidden rounded-full bg-ink-800">
              <span className={`block h-full ${tone}`} style={{ width: `${Math.round(conf * 100)}%` }} />
            </span>
            <span className="w-8 text-right text-[10px] tabular-nums text-ink-400">
              {Math.round(conf * 100)}%
            </span>
          </span>
        ) : null}
      </div>
      <p className={`mt-0.5 text-sm ${value ? "text-ink-50" : "italic text-ink-600"}`}>
        {value ? <Highlighted text={value} lang={lang} /> : "sin datos aún"}
      </p>
    </div>
  );
}

export default function OperatorPage() {
  const elb = useElbRoom("operator");
  const [roomInput, setRoomInput] = useState(DEFAULT_ROOM);
  const [lang, setLang] = useState("es");
  const [elapsed, setElapsed] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef<number>(0);

  useEffect(() => {
    if (!elb.connected) return;
    if (!startedRef.current) startedRef.current = Date.now();
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startedRef.current) / 1000)), 1000);
    return () => clearInterval(t);
  }, [elb.connected]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [elb.entries.length]);

  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
  const e = elb.extraction;

  // ------------------------------------------------------------- pre-connect
  if (!elb.connected) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6 py-12">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-crit-500">
            Puesto de despacho
          </p>
          <h1 className="mt-2 text-2xl font-bold">Consola del operador</h1>
          <p className="mt-2 text-sm text-ink-400">
            Únete a la sala de la llamada. Escucharás únicamente la interpretación hacia
            ti; el audio original del llamante no se mezcla.
          </p>
        </div>

        <label className="block">
          <span className="text-xs font-semibold uppercase tracking-wider text-ink-400">
            Sala de la llamada
          </span>
          <input
            value={roomInput}
            onChange={(ev) => setRoomInput(ev.target.value)}
            autoComplete="off"
            placeholder="p. ej. elb-demo"
            className="mt-1.5 w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm
                       outline-none placeholder:text-ink-600 focus:border-crit-600"
          />
          <span className="mt-1 block text-[11px] text-ink-600">
            Debe coincidir con la sala que muestra la app del llamante.
          </span>
        </label>

        <label className="block">
          <span className="text-xs font-semibold uppercase tracking-wider text-ink-400">
            Tu idioma
          </span>
          <select
            value={lang}
            onChange={(ev) => setLang(ev.target.value)}
            className="mt-1.5 w-full rounded-lg border border-ink-700 bg-ink-900 px-3 py-2.5 text-sm
                       outline-none focus:border-crit-600"
          >
            {OPERATOR_LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>

        {elb.error ? (
          <p className="rounded-lg border border-crit-600 bg-crit-900/40 px-3 py-2 text-sm text-crit-500">
            {elb.error}
          </p>
        ) : null}

        <button
          onClick={() => elb.connect({ room: roomInput.trim() || DEFAULT_ROOM, lang })}
          className="rounded-lg bg-crit-600 px-4 py-3 text-sm font-semibold text-white
                     transition hover:bg-crit-500"
        >
          Entrar a la llamada
        </button>

      </main>
    );
  }

  // ------------------------------------------------------------------ in call
  return (
    <main className="flex h-screen flex-col">
      <div
        ref={elb.audioRef}
        aria-hidden
        style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}
      />
      {elb.micError ? (
        <div className="bg-warn-400 px-4 py-2 text-sm font-semibold text-ink-950">
          {elb.micError}
        </div>
      ) : null}
      {elb.audioBlocked ? (
        <button
          onClick={elb.resumeAudio}
          className="bg-warn-400 px-4 py-2 text-sm font-semibold text-ink-950"
        >
          El navegador bloqueó el audio. Pulsa aquí para escuchar la interpretación.
        </button>
      ) : null}

      {/* header */}
      <header className="flex shrink-0 items-center gap-4 border-b border-ink-800 bg-ink-900 px-4 py-2.5">
        <span className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-crit-500 pulse" />
          <span className="text-sm font-bold tracking-wide">EN LLAMADA</span>
        </span>
        <span className="font-mono text-lg tabular-nums text-ink-50">{clock}</span>
        <span className="rounded border border-ink-700 px-2 py-0.5 font-mono text-[11px] text-ink-400">
          {elb.roomName}
        </span>
        <span className="text-xs text-ink-400">
          {elb.call.callerName ? `${elb.call.callerName} · ` : ""}
          {(elb.call.callerLang ?? "?").toUpperCase()} ↔ {(elb.call.operatorLang ?? lang).toUpperCase()}
        </span>
        {!elb.agentPresent ? (
          <span className="rounded bg-warn-900 px-2 py-0.5 text-[11px] text-warn-400">
            esperando intérprete…
          </span>
        ) : null}

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={elb.toggleMic}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              elb.micOn
                ? "bg-ink-700 text-ink-50 hover:bg-ink-600"
                : "bg-crit-900 text-crit-500 hover:bg-crit-600 hover:text-white"
            }`}
          >
            {elb.micOn ? "🎙 Micrófono activo" : "🔇 Micrófono silenciado"}
          </button>
          <button
            onClick={elb.endCall}
            className="rounded-lg bg-crit-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-crit-500"
          >
            Finalizar y generar reporte
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_400px]">
        {/* transcript */}
        <section className="flex min-h-0 flex-col border-r border-ink-800">
          <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-400">
              Transcripción en vivo
            </h2>
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1.5">
                <span className="text-[10px] uppercase text-ink-600">→ operador</span>
                <StateDot state={elb.dirState.to_operator} />
              </span>
              <span className="flex items-center gap-1.5">
                <span className="text-[10px] uppercase text-ink-600">→ llamante</span>
                <StateDot state={elb.dirState.to_caller} />
              </span>
            </div>
          </div>

          <div ref={scrollRef} className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {elb.entries.length === 0 ? (
              <p className="mt-8 text-center text-sm text-ink-600">
                Esperando que alguien hable…
              </p>
            ) : null}
            {elb.entries.map((entry) => (
              <Bubble key={entry.key} entry={entry} />
            ))}
          </div>

          <footer className="flex shrink-0 items-center gap-4 border-t border-ink-800 px-4 py-2 text-[11px] text-ink-600">
            <span>
              Latencia mediana{" "}
              <b className="tabular-nums text-ink-200">{elb.medianLatency || "–"} ms</b>
            </span>
            <span>glosario v{GLOSSARY_VERSION}</span>
            <span className="ml-auto">
            </span>
          </footer>
        </section>

        {/* dispatch card */}
        <aside className="scroll-thin min-h-0 overflow-y-auto bg-ink-900 p-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-400">
            Ficha de despacho
          </h2>

          {elb.criticalFlags.length > 0 ? (
            <div className="mt-3 rounded-lg border border-crit-600 bg-crit-900/40 p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-crit-500">
                Términos críticos detectados
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {elb.criticalFlags.map((f) => (
                  <span
                    key={f}
                    className="rounded bg-crit-600 px-2 py-0.5 text-[11px] font-bold text-white"
                  >
                    {fixedFor(f, elb.call.operatorLang ?? lang)}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-3 rounded-lg border border-ink-700 bg-ink-850 px-3">
            <Field label="Tipo" node={e?.emergency_type} lang={lang} />
            <Field label="Qué pasó" node={e?.emergency_detail} lang={lang} />
            <Field label="Ubicación" node={e?.location} lang={lang} />
            <Field label="Referencia de acceso" node={e?.location_detail} lang={lang} />
            <Field label="Personas afectadas" node={e?.victim_count} lang={lang} />
            <Field label="Quién llama" node={e?.caller_relationship} lang={lang} />
          </div>

          {e?.victims?.length ? (
            <div className="mt-3 rounded-lg border border-ink-700 bg-ink-850 p-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
                Personas
              </p>
              <ul className="mt-2 space-y-1.5">
                {e.victims.map((v, i) => (
                  <li key={i} className="text-sm">
                    <span className="text-ink-50">{v.who}</span>
                    {v.is_child ? (
                      <span className="ml-1.5 rounded bg-warn-900 px-1.5 py-0.5 text-[10px] font-bold text-warn-400">
                        MENOR
                      </span>
                    ) : null}
                    {v.condition ? (
                      <span className="block text-xs text-ink-400">
                        <Highlighted text={v.condition} lang={lang} />
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {e?.hazards?.length ? (
            <div className="mt-3 rounded-lg border border-warn-400/40 bg-warn-900/30 p-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-warn-400">
                Peligros en el lugar
              </p>
              <ul className="mt-1.5 space-y-0.5 text-sm text-ink-50">
                {e.hazards.map((h, i) => (
                  <li key={i}>• {h}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* support-network status */}
          <div className="mt-3 rounded-lg border border-ink-700 bg-ink-850 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
              Red de apoyo
            </p>
            {elb.notifications.length === 0 ? (
              <p className="mt-1.5 text-xs text-ink-600">
                {elb.notifyState.armed ? "Enviando…" : `En espera — ${elb.notifyState.reason}`}
              </p>
            ) : (
              <ul className="mt-1.5 space-y-1 text-xs">
                {elb.notifications.map((n, i) => {
                  // Three states, not two. "links minted, no channel to send
                  // them on" is neither green nor red: nothing failed, so
                  // painting it red is what trains a dispatcher to ignore red.
                  const label = n.kind === "early" ? "Aviso temprano" : "Reporte final";
                  if (!n.ok) {
                    return (
                      <li key={i} className="text-crit-500">
                        {label} · falló — {n.reason ?? "error de envío"}
                      </li>
                    );
                  }
                  if (n.delivered > 0) {
                    return (
                      <li key={i} className="text-ok-400">
                        {label} · {n.delivered} contacto(s) notificado(s)
                      </li>
                    );
                  }
                  return (
                    <li key={i} className="text-warn-400">
                      {label} · {n.prepared ?? 0} enlace(s) generado(s), sin envío
                      {n.reason ? ` — ${n.reason}` : ""}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {elb.call.operatorUrl ? (
            <a
              href={elb.call.operatorUrl}
              target="_blank"
              rel="noreferrer"
              className="mt-3 block rounded-lg bg-ink-700 px-3 py-2.5 text-center text-sm font-semibold
                         text-ink-50 hover:bg-ink-600"
            >
              Abrir reporte completo →
            </a>
          ) : null}
        </aside>
      </div>
    </main>
  );
}

function Bubble({ entry }: { entry: TranscriptEntry }) {
  const isCaller = entry.speaker === "caller";
  return (
    <div className={`slide-in flex ${isCaller ? "justify-start" : "justify-end"}`}>
      <div
        className={`max-w-[85%] rounded-xl border px-3 py-2 ${
          isCaller ? "border-ink-700 bg-ink-850" : "border-ink-700/60 bg-ink-900"
        }`}
      >
        <div className="flex items-center gap-2">
          <span
            className={`text-[10px] font-bold uppercase tracking-wider ${
              isCaller ? "text-info-400" : "text-ink-400"
            }`}
          >
            {isCaller ? "Llamante" : "Tú"} · {entry.lang}
          </span>
          {entry.fallback ? (
            <span className="rounded bg-crit-900 px-1.5 py-0.5 text-[9px] font-bold text-crit-500">
              MODO RESPALDO
            </span>
          ) : null}
          {entry.repaired?.length ? (
            <span
              className="rounded bg-warn-900 px-1.5 py-0.5 text-[9px] font-bold text-warn-400"
              title="El glosario corrigió la traducción del modelo"
            >
              GLOSARIO ✓
            </span>
          ) : null}
          {entry.latencyMs ? (
            <span className="ml-auto text-[10px] tabular-nums text-ink-600">
              {entry.latencyMs} ms
            </span>
          ) : null}
        </div>

        {/* Original, dimmed: context, not the thing being read. */}
        <p className="mt-1 text-xs italic text-ink-400">
          <Highlighted text={entry.text} lang={entry.lang} />
        </p>

        {/* Interpretation, primary. */}
        {entry.rendered ? (
          <p className="mt-1 text-[15px] leading-snug text-ink-50">
            <Highlighted text={entry.rendered} lang={entry.renderedLang ?? "es"} />
          </p>
        ) : (
          <p className="mt-1 text-xs text-ink-600 pulse">interpretando…</p>
        )}
      </div>
    </div>
  );
}
