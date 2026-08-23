"""Critical-term glossary: detection, prompt injection and post-translation repair.

One list, three jobs:
  1. Build the LOCKED TERMS block injected into the translator's instructions.
  2. Detect critical terms in a source utterance (drives operator highlighting).
  3. Verify the model's output actually carries the mandated target rendering,
     and repair it deterministically when it does not.

The same JSON file feeds the operator console and the Android app, so there is
exactly one definition of "what matters" in the whole system.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def normalize(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace, unify apostrophes.

    Match phrases in critical_terms.json are stored already-normalized, so a
    caller saying "no está respirando" matches the stored "no esta respirando".
    """
    text = text.replace("’", "'").replace("ʼ", "'")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s'-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Term:
    id: str
    severity: str
    polarity: str
    pair: str | None
    fixed: dict[str, str]
    match: dict[str, list[str]]


@dataclass(frozen=True)
class Hit:
    term_id: str
    severity: str
    phrase: str
    start: int
    end: int
    fixed_target: str

    def as_dict(self) -> dict:
        return {
            "term_id": self.term_id,
            "severity": self.severity,
            "phrase": self.phrase,
            "start": self.start,
            "end": self.end,
            "fixed": self.fixed_target,
        }


class Glossary:
    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.version: str = raw["version"]
        self.languages: list[str] = raw["languages"]
        self.terms: dict[str, Term] = {}
        for t in raw["terms"]:
            self.terms[t["id"]] = Term(
                id=t["id"],
                severity=t["severity"],
                polarity=t["polarity"],
                pair=t.get("pair"),
                fixed=t["fixed"],
                match=t["match"],
            )
        # Longest phrase first so "no hay pulso" wins over "pulso".
        self._patterns: dict[str, list[tuple[str, str, re.Pattern[str]]]] = {}
        for lang in self.languages:
            entries: list[tuple[str, str, re.Pattern[str]]] = []
            for term in self.terms.values():
                for phrase in term.match.get(lang, []):
                    p = normalize(phrase)
                    if not p:
                        continue
                    entries.append((term.id, p, re.compile(rf"(?<!\w){re.escape(p)}(?!\w)")))
            entries.sort(key=lambda e: len(e[1]), reverse=True)
            self._patterns[lang] = entries

    def supports(self, lang: str) -> bool:
        return lang in self.languages

    # -- 1. detection -------------------------------------------------------
    def detect(self, text: str, lang: str) -> list[Hit]:
        """Find critical terms in `text` (written in `lang`).

        Overlapping matches are resolved longest-first, so a negation such as
        "no tiene pulso" never degrades into the affirmative "pulso".
        """
        if not text or lang not in self._patterns:
            return []
        norm = normalize(text)
        claimed: list[tuple[int, int]] = []
        hits: list[Hit] = []
        for term_id, phrase, pattern in self._patterns[lang]:
            for m in pattern.finditer(norm):
                s, e = m.span()
                if any(s < ce and cs < e for cs, ce in claimed):
                    continue
                claimed.append((s, e))
                term = self.terms[term_id]
                hits.append(
                    Hit(
                        term_id=term_id,
                        severity=term.severity,
                        phrase=phrase,
                        start=s,
                        end=e,
                        fixed_target="",
                    )
                )
        hits.sort(key=lambda h: h.start)
        return hits

    def detect_for(self, text: str, src_lang: str, dst_lang: str) -> list[Hit]:
        """Detection plus the mandated rendering in the destination language."""
        out = []
        for h in self.detect(text, src_lang):
            fixed = self.terms[h.term_id].fixed.get(dst_lang, "")
            out.append(
                Hit(h.term_id, h.severity, h.phrase, h.start, h.end, fixed)
            )
        return out

    # -- 2. prompt injection ------------------------------------------------
    def locked_block(self, src_lang: str, dst_lang: str) -> str:
        """The LOCKED TERMS table injected verbatim into agent instructions."""
        lines: list[str] = []
        for term in self.terms.values():
            src_phrases = term.match.get(src_lang, [])
            dst_fixed = term.fixed.get(dst_lang)
            if not src_phrases or not dst_fixed:
                continue
            sample = " | ".join(src_phrases[:5])
            flag = "!!" if term.severity == "critical" else "  "
            lines.append(f"{flag} [{sample}] -> MUST render exactly as: {dst_fixed}")
        return "\n".join(lines)

    # -- 3. post-translation repair ----------------------------------------
    def enforce(
        self, source_text: str, translated: str, src_lang: str, dst_lang: str
    ) -> tuple[str, list[str]]:
        """Guarantee every life-critical term detected in the source survives.

        Returns (possibly repaired text, list of repaired term ids). Repair is
        additive - we never delete model output, we append an explicit clause -
        because dropping a negation is the failure mode that kills people, and
        a slightly clumsy sentence is strictly better than a lost "NOT".

        We deliberately do NOT force the literal string for every term. A good
        translation of "my son" is "mi hijo", and stapling "MENOR DE EDAD" onto
        it adds noise without adding information. Two cases justify overriding
        the model:

          * the term is severity=critical and its mandated rendering is absent
          * the OPPOSITE polarity leaked in ("is breathing" -> "no respira"),
            which is the one failure that must never reach a dispatcher

        Everything else is left alone and merely highlighted in the UI.
        """
        hits = self.detect(source_text, src_lang)
        if not hits:
            return translated, []

        norm_out = normalize(translated)
        repaired: list[str] = []
        additions: list[str] = []
        seen: set[str] = set()

        for hit in hits:
            term = self.terms[hit.term_id]
            if term.id in seen:
                continue
            seen.add(term.id)
            required = term.fixed.get(dst_lang)
            if not required:
                continue
            if normalize(required) in norm_out:
                continue

            opposite = self.terms.get(term.pair) if term.pair else None
            opp_fixed = opposite.fixed.get(dst_lang) if opposite else None
            polarity_flipped = bool(opp_fixed) and normalize(opp_fixed) in norm_out

            if term.severity != "critical" and not polarity_flipped:
                continue

            additions.append(required)
            repaired.append(term.id)

        if not additions:
            return translated, []

        suffix = " [" + "; ".join(additions) + "]"
        return translated.rstrip() + suffix, repaired

    def contradicts(self, source_text: str, translated: str, src_lang: str, dst_lang: str) -> list[str]:
        """Term ids whose OPPOSITE polarity leaked into the translation."""
        bad: list[str] = []
        norm_out = normalize(translated)
        for hit in self.detect(source_text, src_lang):
            term = self.terms[hit.term_id]
            if not term.pair:
                continue
            opposite = self.terms.get(term.pair)
            if not opposite:
                continue
            opp_fixed = opposite.fixed.get(dst_lang)
            mine = term.fixed.get(dst_lang)
            if not opp_fixed or not mine:
                continue
            if normalize(opp_fixed) in norm_out and normalize(mine) not in norm_out:
                bad.append(term.id)
        return bad


@lru_cache(maxsize=4)
def load_glossary(path: str) -> Glossary:
    return Glossary(Path(path))
