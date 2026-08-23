"""Plain-text report builder.

DECISION: hand-rolled fixed-width builder on the stdlib (`textwrap`), no
template engine. See docs/DECISIONS.md. A .txt emergency record has a small
fixed set of sections; what it actually needs is byte-stable output that reads
correctly in Notepad, a terminal, an email client and a printout. Jinja2
would add a dependency and whitespace-control friction to buy flexibility we
do not want here - the layout being rigid is a feature.

DATA MINIMISATION is enforced here, at render time, by `Scope`:

  Scope.OPERATOR  full record: clinical detail, confidences, interpretation
                  quality, and the complete bilingual transcript.
  Scope.FAMILY    what a relative needs to act: what happened, where, how many
                  people, on-site hazards, current status. No clinical
                  conditions, no confidence scores, no transcript.

The family variant is not a redacted operator report - it is generated
separately and the excluded fields are never written into it, so they cannot
leak through a rendering bug.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

WIDTH = 80
INNER = 76

RULE = "=" * WIDTH
THIN = "-" * WIDTH


class Scope(str, Enum):
    OPERATOR = "operator"
    FAMILY = "family"


DISCLAIMER = {
    "es": [
        "Este reporte INFORMA, no INSTRUYE.",
        "No contiene ni sustituye indicaciones medicas, de rescate o de seguridad.",
        "No realice procedimientos por su cuenta.",
        "Emergency Language Bridge no reemplaza a los servicios de emergencia",
        "profesionales. Si usted esta en peligro, llame al 123 (Colombia),",
        "911 o 112 segun su pais.",
    ],
    "en": [
        "This report INFORMS, it does not INSTRUCT.",
        "It contains no medical, rescue or safety procedures, and is not a",
        "substitute for them. Do not attempt any procedure on your own.",
        "Emergency Language Bridge does not replace professional emergency",
        "services. If you are in danger, call 123 (Colombia), 911 or 112.",
    ],
}

LABELS = {
    "es": {
        "title": "REPORTE DE LLAMADA DE EMERGENCIA",
        "report_id": "REPORTE ID",
        "generated": "GENERADO",
        "call_start": "INICIO LLAMADA",
        "duration": "DURACION",
        "status": "ESTADO",
        "languages": "IDIOMAS",
        "sec_summary": "RESUMEN",
        "sec_dispatch": "DATOS DE LA EMERGENCIA",
        "sec_people": "PERSONAS AFECTADAS",
        "sec_hazards": "PELIGROS EN EL LUGAR",
        "sec_flags": "TERMINOS CRITICOS DETECTADOS",
        "sec_quality": "CALIDAD DE LA INTERPRETACION",
        "sec_transcript": "TRANSCRIPCION COMPLETA",
        "sec_notice": "AVISO IMPORTANTE",
        "type": "TIPO",
        "detail": "DETALLE",
        "location": "UBICACION",
        "loc_detail": "REFERENCIA",
        "map": "MAPA",
        "victims": "PERSONAS",
        "relationship": "QUIEN LLAMA",
        "severity": "SEVERIDAD",
        "confidence": "confianza",
        "none": "(no informado)",
        "caller": "LLAMANTE",
        "operator": "OPERADOR",
        "interp": "INTERPRETE",
        "turns": "Turnos interpretados",
        "median": "Latencia mediana",
        "p90": "Latencia p90",
        "fallbacks": "Turnos en modo respaldo",
        "repairs": "Correcciones de glosario",
        "partial": "PARCIAL - llamada en curso",
        "final": "FINAL - llamada finalizada",
        "family_note": (
            "Usted recibe este aviso porque fue registrado como contacto de "
            "confianza. Se omiten los detalles clinicos, que quedan unicamente "
            "para el personal de emergencia."
        ),
    },
    "en": {
        "title": "EMERGENCY CALL REPORT",
        "report_id": "REPORT ID",
        "generated": "GENERATED",
        "call_start": "CALL START",
        "duration": "DURATION",
        "status": "STATUS",
        "languages": "LANGUAGES",
        "sec_summary": "SUMMARY",
        "sec_dispatch": "EMERGENCY DETAILS",
        "sec_people": "PEOPLE AFFECTED",
        "sec_hazards": "ON-SITE HAZARDS",
        "sec_flags": "CRITICAL TERMS DETECTED",
        "sec_quality": "INTERPRETATION QUALITY",
        "sec_transcript": "FULL TRANSCRIPT",
        "sec_notice": "IMPORTANT NOTICE",
        "type": "TYPE",
        "detail": "DETAIL",
        "location": "LOCATION",
        "loc_detail": "ACCESS NOTE",
        "map": "MAP",
        "victims": "PEOPLE",
        "relationship": "CALLER IS",
        "severity": "SEVERITY",
        "confidence": "confidence",
        "none": "(not stated)",
        "caller": "CALLER",
        "operator": "OPERATOR",
        "interp": "INTERPRETER",
        "turns": "Interpreted turns",
        "median": "Median latency",
        "p90": "p90 latency",
        "fallbacks": "Turns in fallback mode",
        "repairs": "Glossary repairs",
        "partial": "PARTIAL - call in progress",
        "final": "FINAL - call ended",
        "family_note": (
            "You are receiving this because you were registered as a trusted "
            "contact. Clinical details are withheld and remain with emergency "
            "personnel only."
        ),
    },
}


@dataclass
class TranscriptLine:
    t_offset_s: float
    speaker: str          # "caller" | "operator"
    lang: str
    text: str
    rendered: str = ""
    rendered_lang: str = ""
    fallback: bool = False
    hits: list[str] = field(default_factory=list)


@dataclass
class ReportData:
    report_id: str
    call_id: str
    started_at: datetime
    ended_at: datetime | None
    caller_lang: str
    operator_lang: str
    caller_name: str = ""
    extraction: dict[str, Any] = field(default_factory=dict)
    critical_flags: list[str] = field(default_factory=list)
    transcript: list[TranscriptLine] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    fallback_turns: int = 0
    repaired_terms: list[str] = field(default_factory=list)
    gps: tuple[float, float] | None = None
    is_final: bool = True


# ---------------------------------------------------------------- primitives
def _kv(label: str, value: str, note: str = "", width: int = 14) -> str:
    value = value or ""
    head = f"{label:<{width}}: "
    body = textwrap.wrap(value, width=WIDTH - len(head)) or [""]
    lines = [head + body[0]]
    for extra in body[1:]:
        lines.append(" " * len(head) + extra)
    if note:
        lines[0] = lines[0].ljust(WIDTH - len(note) - 1)[: WIDTH - len(note) - 1] + " " + note
    return "\n".join(lines)


def _section(n: int, title: str) -> str:
    return f"{THIN}\n  {n}. {title}\n{THIN}"


def _para(text: str, indent: str = "  ") -> str:
    if not text:
        return f"{indent}-"
    out = []
    for block in text.split("\n"):
        out.extend(textwrap.wrap(block, width=INNER) or [""])
    return "\n".join(indent + line for line in out)


def _clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _pct(v: Any) -> str:
    try:
        return f"{float(v):.0%}"
    except (TypeError, ValueError):
        return "?"


def _assessed(d: dict, key: str) -> tuple[str, float, str]:
    node = (d or {}).get(key) or {}
    if not isinstance(node, dict):
        return (str(node), 0.0, "")
    return (
        str(node.get("value") or "").strip(),
        float(node.get("confidence") or 0.0),
        str(node.get("evidence") or "").strip(),
    )


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
    return ordered[idx]


# -------------------------------------------------------------------- builder
def build_report(data: ReportData, scope: Scope, lang: str | None = None) -> str:
    lang = lang or (data.operator_lang if scope is Scope.OPERATOR else data.caller_lang)
    L = LABELS.get(lang) or LABELS["en"]
    disc = DISCLAIMER.get(lang) or DISCLAIMER["en"]
    e = data.extraction or {}

    end = data.ended_at or datetime.now(timezone.utc)
    duration = (end - data.started_at).total_seconds()

    out: list[str] = []
    out.append(RULE)
    out.append(f"  EMERGENCY LANGUAGE BRIDGE - {L['title']}")
    out.append(f"  {disc[0]}")
    out.append(RULE)
    out.append("")

    out.append(_kv(L["report_id"], data.report_id))
    out.append(_kv(L["generated"], datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")))
    out.append(_kv(L["call_start"], data.started_at.strftime("%Y-%m-%d %H:%M:%S UTC")))
    out.append(_kv(L["duration"], _clock(duration)))
    out.append(_kv(L["status"], L["final"] if data.is_final else L["partial"]))
    out.append(_kv(L["languages"], f"{data.caller_lang} <-> {data.operator_lang}"))
    out.append("")

    n = 0

    # 1. summary -----------------------------------------------------------
    n += 1
    out.append(_section(n, L["sec_summary"]))
    # Family scope NEVER falls back to the operator summary: that text is
    # written in clinical dispatch language and would defeat the whole point.
    if scope is Scope.FAMILY:
        summary = e.get("summary_family") or L["none"]
    else:
        summary = e.get("summary_operator") or L["none"]
    out.append(_para(summary))
    if scope is Scope.FAMILY:
        out.append("")
        out.append(_para(L["family_note"]))
    out.append("")

    # 2. dispatch data -----------------------------------------------------
    n += 1
    out.append(_section(n, L["sec_dispatch"]))
    show_conf = scope is Scope.OPERATOR

    for key, label in (
        ("emergency_type", L["type"]),
        ("emergency_detail", L["detail"]),
        ("location", L["location"]),
        ("location_detail", L["loc_detail"]),
    ):
        value, conf, _ = _assessed(e, key)
        note = f"[{L['confidence']} {_pct(conf)}]" if show_conf and value else ""
        out.append(_kv(label, value or L["none"], note))

    if data.gps:
        lat, lon = data.gps
        out.append(_kv(L["map"], f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"))

    sev = str(e.get("severity") or "").upper()
    if sev:
        out.append(_kv(L["severity"], sev))

    rel, rconf, _ = _assessed(e, "caller_relationship")
    out.append(_kv(L["relationship"], rel or L["none"],
                   f"[{L['confidence']} {_pct(rconf)}]" if show_conf and rel else ""))
    out.append("")

    # 3. people ------------------------------------------------------------
    n += 1
    out.append(_section(n, L["sec_people"]))
    count, cconf, _ = _assessed(e, "victim_count")
    out.append(_kv(L["victims"], count or L["none"],
                   f"[{L['confidence']} {_pct(cconf)}]" if show_conf and count else ""))
    victims = e.get("victims") or []
    if scope is Scope.OPERATOR:
        for i, v in enumerate(victims, 1):
            who = str(v.get("who") or "?")
            cond = str(v.get("condition") or L["none"])
            child = " [MENOR]" if v.get("is_child") else ""
            consc = str(v.get("conscious") or "unknown")
            out.append(f"  {i}. {who}{child}")
            out.append(_para(f"{cond} (consciente: {consc})", indent="     "))
    else:
        # Family scope: presence and count only, never clinical condition.
        minors = sum(1 for v in victims if v.get("is_child"))
        if victims:
            out.append(_para(f"{len(victims)} persona(s) mencionada(s)"
                             + (f", {minors} menor(es) de edad" if minors else "")))
    out.append("")

    # 4. hazards -----------------------------------------------------------
    n += 1
    out.append(_section(n, L["sec_hazards"]))
    hazards = [str(h) for h in (e.get("hazards") or []) if str(h).strip()]
    if hazards:
        for h in hazards:
            out.append(f"  * {h}")
    else:
        out.append(f"  {L['none']}")
    out.append("")

    if scope is Scope.OPERATOR:
        # 5. critical terms -------------------------------------------------
        n += 1
        out.append(_section(n, L["sec_flags"]))
        if data.critical_flags:
            for f_ in data.critical_flags:
                out.append(f"  ! {f_.replace('_', ' ').upper()}")
        else:
            out.append(f"  {L['none']}")
        out.append("")

        # 6. interpretation quality -----------------------------------------
        n += 1
        out.append(_section(n, L["sec_quality"]))
        out.append(_kv(L["turns"], str(len(data.latencies_ms)), width=26))
        out.append(_kv(L["median"], f"{percentile(data.latencies_ms, 0.5)} ms", width=26))
        out.append(_kv(L["p90"], f"{percentile(data.latencies_ms, 0.9)} ms", width=26))
        out.append(_kv(L["fallbacks"], str(data.fallback_turns), width=26))
        out.append(_kv(L["repairs"], str(len(data.repaired_terms)), width=26))
        out.append("")

        # 7. transcript ------------------------------------------------------
        n += 1
        out.append(_section(n, L["sec_transcript"]))
        if not data.transcript:
            out.append(f"  {L['none']}")
        for line in data.transcript:
            who = L["caller"] if line.speaker == "caller" else L["operator"]
            stamp = _clock(line.t_offset_s)
            out.append(f"  [{stamp}] {who} ({line.lang})")
            out.append(_para(line.text, indent="            "))
            if line.rendered:
                tag = L["interp"] + (" [RESPALDO]" if line.fallback else "")
                out.append(f"            -> {tag} ({line.rendered_lang})")
                out.append(_para(line.rendered, indent="               "))
            if line.hits:
                out.append("               ! " + ", ".join(h.replace("_", " ").upper() for h in line.hits))
            out.append("")

    # notice ----------------------------------------------------------------
    n += 1
    out.append(_section(n, L["sec_notice"]))
    for d in disc:
        out.append(_para(d))
    out.append("")
    out.append(RULE)
    out.append(f"  Emergency Language Bridge - {data.report_id} - scope={scope.value}")
    out.append(RULE)
    out.append("")

    return "\n".join(out)


def report_filename(data: ReportData, scope: Scope) -> str:
    stamp = data.started_at.strftime("%Y%m%d-%H%M%S")
    return f"ELB-{stamp}-{data.report_id[:8]}-{scope.value}.txt"
