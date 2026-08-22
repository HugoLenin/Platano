/** Wire format of the LiveKit data-channel bus. Mirrors agent/src/elb/bus.py. */

export const DATA_TOPIC = "elb";

export const IDENTITY_CALLER = "caller";
export const IDENTITY_OPERATOR = "operator";
export const TRACK_TO_OPERATOR = "interpreter-to-operator";
export const TRACK_TO_CALLER = "interpreter-to-caller";

export type TurnState = "listening" | "processing" | "speaking" | "fallback" | "error";

export interface GlossaryHit {
  term_id: string;
  severity: "critical" | "high" | "info";
  phrase: string;
  start: number;
  end: number;
  fixed: string;
}

interface Base { v: 1; ts: number }

export type ElbEvent =
  | (Base & { type: "transcript"; direction: string; speaker: "caller" | "operator";
      lang: string; seq: number; text: string; hits: GlossaryHit[] })
  | (Base & { type: "translation"; direction: string; speaker: "caller" | "operator";
      lang: string; seq: number; source: string; text: string; fallback: boolean;
      repaired: string[]; contradicted: string[]; latency_ms: number })
  | (Base & { type: "state"; direction: string; state: TurnState; fallback: boolean;
      translate_ms: number })
  | (Base & { type: "extraction"; pass_no: number; data: Extraction;
      critical_flags: string[]; notify: boolean; notify_reason: string })
  | (Base & { type: "notify"; kind: "early" | "final"; delivered: number;
      ok?: boolean; reason?: string; detail?: unknown })
  | (Base & { type: "call"; phase: string; call_id?: string; report_id?: string;
      caller_lang?: string; operator_lang?: string; caller_name?: string;
      operator_url?: string; glossary_version?: string })
  | (Base & { type: "metric"; direction: string; metric: string; value: number;
      fallback: boolean });

export interface Assessed { value: string; confidence: number; evidence: string }

export interface Extraction {
  emergency_type: Assessed;
  emergency_detail: Assessed;
  location: Assessed;
  location_detail: Assessed;
  victim_count: Assessed;
  victims: Array<{ who: string; condition: string; is_child: boolean; conscious: string }>;
  hazards: string[];
  caller_relationship: Assessed;
  severity: string;
  summary_operator: string;
  summary_family: string;
}

export function decodeEvent(payload: Uint8Array): ElbEvent | null {
  try {
    const parsed = JSON.parse(new TextDecoder().decode(payload));
    return parsed && parsed.type ? (parsed as ElbEvent) : null;
  } catch {
    return null;
  }
}

/**
 * Returns a Uint8Array backed by a real ArrayBuffer.
 *
 * TextEncoder.encode() is typed as Uint8Array<ArrayBufferLike>, which since
 * TS 5.7 is not assignable to livekit-client's Uint8Array<ArrayBuffer>
 * (ArrayBufferLike admits SharedArrayBuffer). The copy makes the backing store
 * concrete instead of casting the problem away.
 */
export function encodeMessage(msg: Record<string, unknown>): Uint8Array<ArrayBuffer> {
  const encoded = new TextEncoder().encode(JSON.stringify(msg));
  const out = new Uint8Array(new ArrayBuffer(encoded.byteLength));
  out.set(encoded);
  return out;
}
