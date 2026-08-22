/**
 * Support-network delivery fan-out.
 *
 * The Python agent decides WHEN to notify and WHAT the report says, and mints
 * the per-contact signed links. This route only decides HOW each contact is
 * reached, in strict preference order:
 *
 *   1. native push   - the contact has the app installed (richest, has a map)
 *   2. WhatsApp      - template outside the 24h window, free-form + native
 *                      location card inside it
 *   3. email         - last resort, plain link
 *
 * Every attempt is written to `deliveries` with a unique constraint on
 * (report_id, contact_id, kind, channel), so an agent retry cannot notify a
 * frightened relative twice.
 */

import { NextResponse } from "next/server";
import { supabaseAdmin, requireInternalToken } from "@/lib/supabase";
import { sendEmergencyAlert, sessionWindowOpen, whatsappConfigured } from "@/lib/whatsapp";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface ContactIn {
  id: string;
  name: string;
  phone_e164?: string;
  email?: string;
  locale?: string;
  relationship?: string;
  push_token?: string;
  whatsapp_opt_in_at?: string | null;
  link: string;
  link_expires_at?: number;
}

interface Body {
  call_id: string;
  report_id: string;
  kind: "early" | "final";
  caller_name: string;
  emergency_type: string;
  location: string;
  severity: string;
  summary: string;
  lat: number | null;
  lon: number | null;
  contacts: ContactIn[];
}

interface Attempt {
  contact_id: string;
  channel: string;
  ok: boolean;
  detail?: string;
  skipped?: boolean;
}

async function alreadySent(reportId: string, contactId: string, kind: string): Promise<boolean> {
  const sb = supabaseAdmin();
  if (!sb) return false;
  const { data } = await sb
    .from("deliveries")
    .select("id")
    .eq("report_id", reportId)
    .eq("contact_id", contactId)
    .eq("kind", kind)
    .eq("status", "sent")
    .limit(1);
  return Boolean(data && data.length);
}

async function record(row: Record<string, unknown>) {
  const sb = supabaseAdmin();
  if (!sb) return;
  // Upsert, not insert: the unique index is the idempotency guard and a
  // conflict here is expected behaviour, not an error worth logging loudly.
  const { error } = await sb
    .from("deliveries")
    .upsert(row, { onConflict: "report_id,contact_id,kind,channel" });
  if (error) console.warn("[notify] delivery log failed:", error.message);
}

async function sendPush(c: ContactIn, b: Body): Promise<Attempt | null> {
  if (!c.push_token) return null;
  const key = process.env.FCM_SERVER_KEY;
  if (!key) {
    // The app-installed path needs an FCM project. Without one we fall
    // through to WhatsApp rather than silently dropping the contact.
    return null;
  }
  try {
    const res = await fetch("https://fcm.googleapis.com/fcm/send", {
      method: "POST",
      headers: { Authorization: `key=${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        to: c.push_token,
        priority: "high",
        notification: {
          title:
            c.locale?.startsWith("en")
              ? `Emergency alert from ${b.caller_name}`
              : `Alerta de emergencia de ${b.caller_name}`,
          body: `${b.emergency_type || "-"} - ${b.location || "-"}`,
        },
        data: {
          report_id: b.report_id,
          kind: b.kind,
          link: c.link,
          lat: b.lat != null ? String(b.lat) : "",
          lon: b.lon != null ? String(b.lon) : "",
          severity: b.severity,
        },
      }),
    });
    const ok = res.ok;
    return { contact_id: c.id, channel: "push", ok, detail: ok ? undefined : await res.text() };
  } catch (err) {
    return { contact_id: c.id, channel: "push", ok: false, detail: String(err) };
  }
}

async function sendWhatsApp(c: ContactIn, b: Body): Promise<Attempt | null> {
  if (!c.phone_e164 || !whatsappConfigured()) return null;
  const res = await sendEmergencyAlert({
    to: c.phone_e164,
    contactName: c.name,
    callerName: b.caller_name,
    emergencyType: b.emergency_type,
    location: b.location,
    locale: c.locale || "es",
    link: c.link,
    lat: b.lat,
    lon: b.lon,
    optInAt: c.whatsapp_opt_in_at,
  });
  if (res.skipped) return null;
  return {
    contact_id: c.id,
    channel: "whatsapp",
    ok: res.ok,
    detail: res.ok
      ? `${res.usedTemplate ? "template" : "freeform"} ${res.messageId ?? ""}`.trim()
      : res.error,
  };
}

async function sendEmail(c: ContactIn, b: Body): Promise<Attempt | null> {
  const key = process.env.RESEND_API_KEY;
  if (!c.email || !key) return null;
  const es = (c.locale || "es").startsWith("es");
  const subject = es
    ? `Alerta de emergencia - ${b.caller_name}`
    : `Emergency alert - ${b.caller_name}`;
  const html = `
    <div style="font-family:system-ui,sans-serif;max-width:560px">
      <h2 style="color:#b91c1c">${es ? "Alerta de emergencia" : "Emergency alert"}</h2>
      <p>${es
        ? `<b>${b.caller_name}</b> activó una alerta y te registró como contacto de confianza.`
        : `<b>${b.caller_name}</b> triggered an alert and listed you as a trusted contact.`}</p>
      <p><b>${es ? "Tipo" : "Type"}:</b> ${b.emergency_type || "-"}<br/>
         <b>${es ? "Lugar" : "Place"}:</b> ${b.location || "-"}</p>
      <p><a href="${c.link}" style="background:#b91c1c;color:#fff;padding:10px 16px;
        border-radius:8px;text-decoration:none">${es ? "Ver reporte" : "View report"}</a></p>
      <p style="font-size:12px;color:#666">${es
        ? "Este aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia profesionales. Si hay peligro, llama al 123."
        : "This message informs, it does not instruct. It does not replace professional emergency services. If there is danger, call 123 / 911 / 112."}</p>
    </div>`;
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        from: process.env.RESEND_FROM || "ELB <onboarding@resend.dev>",
        to: [c.email],
        subject,
        html,
      }),
    });
    return { contact_id: c.id, channel: "email", ok: res.ok, detail: res.ok ? undefined : await res.text() };
  } catch (err) {
    return { contact_id: c.id, channel: "email", ok: false, detail: String(err) };
  }
}

export async function POST(req: Request) {
  if (!requireInternalToken(req)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  if (!body.report_id || !Array.isArray(body.contacts)) {
    return NextResponse.json({ error: "report_id and contacts are required" }, { status: 400 });
  }

  const attempts: Attempt[] = [];

  await Promise.all(
    body.contacts.map(async (c) => {
      if (await alreadySent(body.report_id, c.id, body.kind)) {
        attempts.push({ contact_id: c.id, channel: "-", ok: true, skipped: true, detail: "already delivered" });
        return;
      }

      // Preference order. Stop at the first channel that actually succeeds so
      // a relative does not get the same alarm three times.
      const ladder = [sendPush, sendWhatsApp, sendEmail];
      let delivered = false;
      for (const send of ladder) {
        const attempt = await send(c, body);
        if (!attempt) continue;
        attempts.push(attempt);
        await record({
          report_id: body.report_id,
          contact_id: c.id,
          kind: body.kind,
          channel: attempt.channel,
          status: attempt.ok ? "sent" : "failed",
          error: attempt.ok ? null : (attempt.detail ?? "").slice(0, 500),
        });
        if (attempt.ok) {
          delivered = true;
          break;
        }
      }
      if (!delivered) {
        attempts.push({ contact_id: c.id, channel: "-", ok: false, detail: "no channel available" });
        await record({
          report_id: body.report_id,
          contact_id: c.id,
          kind: body.kind,
          channel: "none",
          status: "failed",
          error: "no channel available (no push token, no whatsapp, no email)",
        });
      }
    }),
  );

  const delivered = attempts.filter((a) => a.ok && !a.skipped).length;
  console.log(
    `[notify] report=${body.report_id} kind=${body.kind} delivered=${delivered}/${body.contacts.length}`,
  );
  return NextResponse.json({ ok: true, delivered, attempts });
}
