/**
 * Verifier for the short-lived report links minted by the Python agent.
 *
 * This MUST stay byte-compatible with agent/src/elb/signing.py:
 *   token   = "v1." + b64url(payload) + "." + b64url(hmac_sha256(secret, "v1." + b64url(payload)))
 *   payload = JSON with keys r, c, s, exp, n - separators (",",":"), keys sorted
 *
 * Uses Web Crypto so it runs on the Edge runtime as well as Node.
 */

export const SCOPE_FAMILY = "family";
export const SCOPE_OPERATOR = "operator";
export type Scope = typeof SCOPE_FAMILY | typeof SCOPE_OPERATOR;

export interface LinkClaims {
  reportId: string;
  contactId: string;
  scope: Scope;
  exp: number;
  nonce: string;
}

export class BadToken extends Error {}

function b64urlDecode(s: string): Uint8Array {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Constant-time comparison; short-circuiting here would leak the signature. */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmac(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return b64urlEncode(new Uint8Array(sig));
}

export async function verifyToken(secret: string, token: string): Promise<LinkClaims> {
  if (!secret) throw new BadToken("server is missing ELB_REPORT_SIGNING_SECRET");

  const parts = token.split(".");
  if (parts.length !== 3) throw new BadToken("malformed token");
  const [version, body, sig] = parts;
  if (version !== "v1") throw new BadToken(`unsupported version ${version}`);

  const expected = await hmac(secret, `${version}.${body}`);
  if (!timingSafeEqual(expected, sig)) throw new BadToken("bad signature");

  let data: Record<string, unknown>;
  try {
    data = JSON.parse(new TextDecoder().decode(b64urlDecode(body)));
  } catch {
    throw new BadToken("malformed payload");
  }

  const scope = String(data.s);
  if (scope !== SCOPE_FAMILY && scope !== SCOPE_OPERATOR) throw new BadToken("unknown scope");

  const claims: LinkClaims = {
    reportId: String(data.r),
    contactId: String(data.c),
    scope,
    exp: Number(data.exp),
    nonce: String(data.n),
  };
  if (!Number.isFinite(claims.exp)) throw new BadToken("malformed payload");
  if (Date.now() / 1000 > claims.exp) throw new BadToken("expired");
  return claims;
}

/** Minted server-side only (operator "open full report" button). */
export async function mintToken(
  secret: string,
  opts: { reportId: string; contactId: string; scope: Scope; ttlSeconds: number },
): Promise<string> {
  const payload = {
    c: opts.contactId,
    exp: Math.floor(Date.now() / 1000) + opts.ttlSeconds,
    n: b64urlEncode(crypto.getRandomValues(new Uint8Array(8))),
    r: opts.reportId,
    s: opts.scope,
  };
  // Key order must match Python's sort_keys=True: c, exp, n, r, s
  const json = `{"c":${JSON.stringify(payload.c)},"exp":${payload.exp},"n":${JSON.stringify(
    payload.n,
  )},"r":${JSON.stringify(payload.r)},"s":${JSON.stringify(payload.s)}}`;
  const body = b64urlEncode(new TextEncoder().encode(json));
  const sig = await hmac(secret, `v1.${body}`);
  return `v1.${body}.${sig}`;
}
