import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let cached: SupabaseClient | null = null;

/**
 * Service-role client for server routes only. Never import this from a
 * "use client" module - the key must never reach a browser bundle.
 */
export function supabaseAdmin(): SupabaseClient | null {
  if (cached) return cached;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    console.warn("[supabase] not configured - running in memory-only mode");
    return null;
  }
  cached = createClient(url, key, { auth: { persistSession: false } });
  return cached;
}

export function requireInternalToken(req: Request): boolean {
  const expected = process.env.ELB_INTERNAL_TOKEN;
  if (!expected) return true; // dev convenience; set it in any shared environment
  return req.headers.get("x-elb-internal-token") === expected;
}
