/**
 * Inbound WhatsApp webhook - this is what makes the opt-in flow real.
 *
 * When a trusted contact sends ANY message to our WhatsApp number, Meta
 * delivers it here. We do two things with it:
 *
 *   1. Record `whatsapp_opt_in_at`. That is auditable consent, and it is also
 *      what opens the 24-hour session window that lets us send a free-form
 *      alert (with a native location card) instead of a rigid template.
 *   2. Honour opt-out keywords immediately, no confirmation loop.
 *
 * GET is Meta's subscription handshake.
 */

import { NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabase";
import { sendOptInConfirmation } from "@/lib/whatsapp";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const OPT_OUT = /^(baja|stop|salir|unsubscribe|cancelar|no)$/i;

export async function GET(req: Request) {
  const url = new URL(req.url);
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");

  if (mode === "subscribe" && token === process.env.WHATSAPP_WEBHOOK_VERIFY_TOKEN) {
    return new Response(challenge ?? "", { status: 200 });
  }
  return new Response("forbidden", { status: 403 });
}

interface WaMessage {
  from?: string;
  id?: string;
  type?: string;
  text?: { body?: string };
}

export async function POST(req: Request) {
  const payload = await req.json().catch(() => null);
  if (!payload) return NextResponse.json({ ok: true });

  const messages: WaMessage[] = [];
  for (const entry of payload.entry ?? []) {
    for (const change of entry.changes ?? []) {
      for (const m of change.value?.messages ?? []) messages.push(m as WaMessage);
    }
  }
  if (!messages.length) return NextResponse.json({ ok: true });

  const sb = supabaseAdmin();
  if (!sb) return NextResponse.json({ ok: true, note: "supabase not configured" });

  for (const msg of messages) {
    const from = (msg.from || "").replace(/[^\d]/g, "");
    if (!from) continue;

    const text = (msg.text?.body || "").trim();
    const optingOut = OPT_OUT.test(text);

    // Match on the last 10 digits: contacts are stored E.164 but WhatsApp
    // reports the number without a "+", and some countries add a mobile digit.
    const suffix = from.slice(-10);
    const { data: contacts } = await sb
      .from("trusted_contacts")
      .select("id,name,user_id,locale,phone_e164,whatsapp_opt_in_at")
      .like("phone_e164", `%${suffix}`);

    if (!contacts?.length) {
      console.log(`[whatsapp] inbound from unknown number ...${suffix}`);
      continue;
    }

    for (const c of contacts) {
      if (optingOut) {
        await sb
          .from("trusted_contacts")
          .update({ active: false, whatsapp_opt_in_at: null })
          .eq("id", c.id);
        console.log(`[whatsapp] ${c.name} opted OUT`);
        continue;
      }

      const wasOptedIn = Boolean(c.whatsapp_opt_in_at);
      await sb
        .from("trusted_contacts")
        .update({
          whatsapp_opt_in_at: new Date().toISOString(),
          whatsapp_opt_in_ref: msg.id ?? null,
          active: true,
        })
        .eq("id", c.id);

      if (!wasOptedIn) {
        const { data: profile } = await sb
          .from("profiles")
          .select("display_name")
          .eq("id", c.user_id)
          .single();
        await sendOptInConfirmation(from, c.locale || "es", profile?.display_name || "un contacto");
        console.log(`[whatsapp] ${c.name} opted IN (window open for 24h)`);
      }
    }
  }

  return NextResponse.json({ ok: true });
}
