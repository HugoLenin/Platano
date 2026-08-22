/**
 * Operator-side highlighting, driven by the SAME glossary the agent enforces.
 *
 * lib/critical_terms.json is a build-time copy of shared/critical_terms.json
 * (see scripts/sync-glossary.mjs). There is deliberately no second list: if a
 * term is highlighted here, the interpreter guaranteed it upstream, and if it
 * is not in that file it is not highlighted anywhere.
 */

import raw from "./critical_terms.json";

export type Severity = "critical" | "high" | "info";

export interface Term {
  id: string;
  severity: Severity;
  polarity: "positive" | "negative";
  pair: string | null;
  fixed: Record<string, string>;
  match: Record<string, string[]>;
}

interface GlossaryFile {
  version: string;
  languages: string[];
  terms: Term[];
}

const data = raw as unknown as GlossaryFile;

export const GLOSSARY_VERSION = data.version;
export const LANGUAGES = data.languages;
export const TERMS: Record<string, Term> = Object.fromEntries(
  data.terms.map((t) => [t.id, t]),
);

/** Same normalisation as agent/src/elb/glossary.py: NFD, drop marks, lowercase. */
export function normalize(text: string): string {
  return text
    .replace(/[’ʼ]/g, "'")
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s'-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export interface Segment {
  text: string;
  termId?: string;
  severity?: Severity;
}

interface Compiled {
  termId: string;
  phrase: string;
  re: RegExp;
}

const compiledByLang = new Map<string, Compiled[]>();

function compile(lang: string): Compiled[] {
  const cached = compiledByLang.get(lang);
  if (cached) return cached;
  const out: Compiled[] = [];
  for (const term of data.terms) {
    for (const phrase of term.match[lang] ?? []) {
      const p = normalize(phrase);
      if (!p) continue;
      const escaped = p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      out.push({ termId: term.id, phrase: p, re: new RegExp(`(?<!\\p{L})${escaped}(?!\\p{L})`, "gu") });
    }
  }
  // Longest first so "no tiene pulso" beats "pulso" and negations survive.
  out.sort((a, b) => b.phrase.length - a.phrase.length);
  compiledByLang.set(lang, out);
  return out;
}

/**
 * Split `text` into plain and highlighted segments.
 *
 * Matching happens on the normalised string, but we map spans back onto the
 * ORIGINAL text so the operator reads exactly what was said, accents and all.
 */
export function highlight(text: string, lang: string): Segment[] {
  if (!text) return [];
  if (!LANGUAGES.includes(lang)) return [{ text }];

  // Build a normalised string that preserves a per-character index map.
  const map: number[] = [];
  let norm = "";
  for (let i = 0; i < text.length; i++) {
    const piece = normalize(text[i]);
    if (piece === "") {
      // Character vanished under normalisation (a combining mark). Keep the
      // map aligned by attaching it to the previous output char.
      continue;
    }
    for (const ch of piece) {
      norm += ch;
      map.push(i);
    }
  }
  norm = norm.replace(/\s+/g, " ");

  // Re-derive the map after whitespace collapsing.
  const collapsed: number[] = [];
  let out = "";
  let prevSpace = false;
  for (let i = 0; i < norm.length; i++) {
    const isSpace = norm[i] === " ";
    if (isSpace && prevSpace) continue;
    out += norm[i];
    collapsed.push(map[i] ?? 0);
    prevSpace = isSpace;
  }

  const claimed: Array<[number, number, string]> = [];
  for (const { termId, re } of compile(lang)) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(out)) !== null) {
      const s = m.index;
      const e = m.index + m[0].length;
      if (claimed.some(([cs, ce]) => s < ce && cs < e)) continue;
      claimed.push([s, e, termId]);
    }
  }
  if (!claimed.length) return [{ text }];

  claimed.sort((a, b) => a[0] - b[0]);
  const segments: Segment[] = [];
  let cursor = 0;
  for (const [s, e, termId] of claimed) {
    const origStart = collapsed[s] ?? 0;
    const origEnd = (collapsed[e - 1] ?? text.length - 1) + 1;
    if (origStart > cursor) segments.push({ text: text.slice(cursor, origStart) });
    segments.push({
      text: text.slice(origStart, origEnd),
      termId,
      severity: TERMS[termId]?.severity,
    });
    cursor = origEnd;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) });
  return segments;
}

export function fixedFor(termId: string, lang: string): string {
  return TERMS[termId]?.fixed[lang] ?? termId.replace(/_/g, " ").toUpperCase();
}

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "info"];
