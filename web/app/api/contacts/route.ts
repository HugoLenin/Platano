/**
 * Trusted-contact CRUD, used by the Android settings screen.
 *
 * Contacts live in Supabase rather than on the device on purpose: the whole
 * premise is that this still works when the phone is lost, stolen or dead.
 */

import { NextResponse } from "next/server";
import { supabaseAdmin } from "@/lib/supabase";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function unavailable() {
  return NextResponse.json({ error: "Supabase is not configured" }, { status: 503 });
}

export async function GET(req: Request) {
  const sb = supabaseAdmin();
  if (!sb) return unavailable();
  const userId = new URL(req.url).searchParams.get("user_id");
  if (!userId) return NextResponse.json({ error: "user_id is required" }, { status: 400 });

  const { data, error } = await sb
    .from("trusted_contacts")
    .select("*")
    .eq("user_id", userId)
    .order("priority", { ascending: true });
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  return NextResponse.json({
    contacts: (data ?? []).map((c) => ({
      ...c,
      // Surfaced so the app can show "opt-in pending" instead of pretending
      // the contact is reachable on WhatsApp when they are not.
      whatsapp_ready: Boolean(c.whatsapp_opt_in_at),
    })),
  });
}

export async function POST(req: Request) {
  const sb = supabaseAdmin();
  if (!sb) return unavailable();

  const body = await req.json().catch(() => null);
  if (!body?.user_id || !body?.name) {
    return NextResponse.json({ error: "user_id and name are required" }, { status: 400 });
  }
  if (!body.phone_e164 && !body.email) {
    return NextResponse.json(
      { error: "a contact needs at least a phone number or an email" },
      { status: 400 },
    );
  }

  const row = {
    user_id: body.user_id,
    name: String(body.name).slice(0, 120),
    relationship: String(body.relationship ?? "").slice(0, 60),
    phone_e164: body.phone_e164 ? String(body.phone_e164).replace(/[^\d+]/g, "") : null,
    email: body.email ?? null,
    locale: body.locale ?? "es",
    priority: Number(body.priority ?? 100),
    notify_early: body.notify_early ?? true,
    notify_final: body.notify_final ?? true,
    active: true,
  };

  const { data, error } = await sb.from("trusted_contacts").insert(row).select().single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ contact: data, whatsapp_ready: false }, { status: 201 });
}

export async function DELETE(req: Request) {
  const sb = supabaseAdmin();
  if (!sb) return unavailable();
  const id = new URL(req.url).searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id is required" }, { status: 400 });
  const { error } = await sb.from("trusted_contacts").delete().eq("id", id);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ ok: true });
}
