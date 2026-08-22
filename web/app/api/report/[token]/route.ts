/**
 * Download endpoint for the .txt report.
 *
 * Re-verifies the token independently of the page: a download URL is shareable
 * on its own, so it cannot inherit trust from a page render.
 */

import { supabaseAdmin } from "@/lib/supabase";
import { SCOPE_FAMILY, verifyToken } from "@/lib/signing";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  const secret = process.env.ELB_REPORT_SIGNING_SECRET ?? "";

  let claims;
  try {
    claims = await verifyToken(secret, decodeURIComponent(token));
  } catch (err) {
    const msg = err instanceof Error ? err.message : "invalid token";
    return new Response(msg, { status: msg === "expired" ? 410 : 403 });
  }

  const sb = supabaseAdmin();
  if (!sb) return new Response("storage not configured", { status: 503 });

  const { data } = await sb
    .from("reports")
    .select("id,operator_txt,family_txt,revoked_at")
    .eq("id", claims.reportId)
    .maybeSingle<{ id: string; operator_txt: string; family_txt: string; revoked_at: string | null }>();

  if (!data) return new Response("not found", { status: 404 });
  if (data.revoked_at) return new Response("revoked", { status: 410 });

  const body = claims.scope === SCOPE_FAMILY ? data.family_txt : data.operator_txt;
  const filename = `ELB-${data.id}-${claims.scope}.txt`;

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
    },
  });
}
