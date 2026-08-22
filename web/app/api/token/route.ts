/**
 * LiveKit access tokens for the two human participants.
 *
 * Identities are fixed strings ("caller" / "operator") because the agent binds
 * each translation direction to a specific participant identity, and the
 * clients subscribe by track name. Anything else and the wiring silently
 * breaks.
 */

import { NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const IDENTITIES = new Set(["caller", "operator"]);

export async function POST(req: Request) {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  if (!apiKey || !apiSecret) {
    return NextResponse.json({ error: "LiveKit is not configured on the server" }, { status: 500 });
  }

  let body: Record<string, string> = {};
  try {
    body = await req.json();
  } catch {
    /* allow empty body */
  }

  const role = (body.role || "operator").toLowerCase();
  if (!IDENTITIES.has(role)) {
    return NextResponse.json({ error: `role must be caller or operator` }, { status: 400 });
  }

  const room = (body.room || "").trim() || `elb-${Date.now().toString(36)}`;
  const lang = (body.lang || (role === "operator" ? "es" : "en")).slice(0, 5);

  const at = new AccessToken(apiKey, apiSecret, {
    identity: role,
    name: body.display_name || (role === "operator" ? "Despachador" : "Llamante"),
    ttl: "2h",
    // Read by the agent to configure the translation pair for this call.
    attributes: {
      role,
      lang,
      user_id: body.user_id || "",
      display_name: body.display_name || "",
    },
  });
  at.addGrant({
    room,
    roomJoin: true,
    canPublish: true,
    canSubscribe: true,
    canPublishData: true,
    canUpdateOwnMetadata: true,
  });

  return NextResponse.json({
    token: await at.toJwt(),
    url: process.env.NEXT_PUBLIC_LIVEKIT_URL || process.env.LIVEKIT_URL,
    room,
    identity: role,
  });
}
