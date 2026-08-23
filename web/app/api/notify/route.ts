/**
 * Support-network delivery.
 *
 * The Python agent decides WHEN to notify and WHAT the report says, and mints
 * the per-contact signed links. This route only decides HOW each contact is
 * reached - and as of now there is exactly one way: email over SMTP.
 *
 * It used to be a three-rung ladder (native push -> WhatsApp -> email). Both
 * of the other rungs are gone, and for different reasons worth recording:
 *
 *   - WhatsApp needed a Meta-approved template for any contact who had never
 *     messaged the business number, which is every trusted contact by
 *     definition. The approval, the WABA and the phone number ID were all
 *     upstream of a working demo.
 *   - Native push needed an FCM project AND an Android client that registers a
 *     token. The app never registered one, so `push_token` was always empty and
 *     the rung never once executed. Deleting dead code beats keeping it.
 *
 * Every attempt is written to `deliveries` with a unique constraint on
 * (report_id, contact_id, kind, channel), so an agent retry cannot notify a
 * frightened relative twice.
 */

import { NextResponse } from "next/server";
import { supabaseAdmin, requireInternalToken } from "@/lib/supabase";
import { sendEmergencyAlert, emailConfigured } from "@/lib/email";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface ContactIn {
  id: string;
  name: string;
  phone_e164?: string;
  email?: string;
  locale?: string;
  relationship?: string;
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

/**
 * Channels this deployment can actually use. Kept deliberately separate from
 * "did it work": a channel that was never configured did not fail, it does not
 * exist here, and recording it as a failed delivery makes a correctly-working
 * deployment look broken.
 */
function configuredChannels(): string[] {
  return emailConfigured() ? ["email"] : [];
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

async function sendEmail(c: ContactIn, b: Body): Promise<Attempt | null> {
  if (!c.email || !emailConfigured()) return null;
  const res = await sendEmergencyAlert({
    to: c.email,
    contactName: c.name,
    callerName: b.caller_name,
    emergencyType: b.emergency_type,
    location: b.location,
    locale: c.locale || "es",
    link: c.link,
    lat: b.lat,
    lon: b.lon,
  });
  if (res.skipped) return null;
  return {
    contact_id: c.id,
    channel: "email",
    ok: res.ok,
    detail: res.ok ? res.messageId : res.error,
  };
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
  const channels = configuredChannels();

  await Promise.all(
    body.contacts.map(async (c) => {
      if (await alreadySent(body.report_id, c.id, body.kind)) {
        attempts.push({ contact_id: c.id, channel: "-", ok: true, skipped: true, detail: "already delivered" });
        return;
      }

      // One rung left, but the loop stays: it is the shape that makes adding a
      // second channel a one-line change instead of a rewrite.
      const ladder = [sendEmail];
      const mine: Attempt[] = [];
      let delivered = false;
      for (const send of ladder) {
        const attempt = await send(c, body);
        if (!attempt) continue;
        mine.push(attempt);
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
        // Nothing went out, and there are two very different reasons for that.
        // Only a channel that was tried and errored is a failure; a channel
        // this deployment never configured is `skipped`.
        const tried = mine.some((a) => !a.ok);
        const note = tried
          ? "every available channel failed"
          : channels.length
            ? "contact has no email address"
            : "no delivery channel configured in this deployment";
        mine.push({ contact_id: c.id, channel: "-", ok: false, skipped: !tried, detail: note });
        await record({
          report_id: body.report_id,
          contact_id: c.id,
          kind: body.kind,
          channel: "none",
          status: tried ? "failed" : "skipped",
          error: note,
        });
      }

      attempts.push(...mine);
    }),
  );

  const delivered = attempts.filter((a) => a.ok && !a.skipped).length;
  const failures = attempts.filter((a) => !a.ok && !a.skipped).length;
  const prepared = body.contacts.length;
  const note =
    delivered || failures
      ? undefined
      : channels.length
        ? "no contact was reachable on a configured channel"
        : "delivery channels not configured - links were minted but nothing was sent";

  console.log(
    `[notify] report=${body.report_id} kind=${body.kind} delivered=${delivered}/${prepared}` +
      (failures ? ` failures=${failures}` : "") +
      (channels.length ? ` channels=${channels.join(",")}` : " channels=none"),
  );
  // `ok` means "nothing that was actually attempted failed". An unconfigured
  // channel keeps this true on purpose: email being absent must not surface as
  // a red error on the dispatcher console.
  return NextResponse.json({ ok: failures === 0, delivered, prepared, failures, channels, note, attempts });
}
