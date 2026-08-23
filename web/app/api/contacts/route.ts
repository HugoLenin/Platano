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

  // Email is the only delivery channel, and an address is either present or
  // it is not - there is no opt-in handshake left to report on.
  return NextResponse.json({
    contacts: (data ?? []).map((c) => ({ ...c, email_ready: Boolean(c.email) })),
  });
}

export async function POST(req: Request) {
  const sb = supabaseAdmin();
  if (!sb) return unavailable();

  const body = await req.json().catch(() => null);
  if (!body?.user_id || !body?.name) {
    return NextResponse.json({ error: "user_id and name are required" }, { status: 400 });
  }
  // Email is the only way an alert can leave this system, so a contact
  // without one cannot be notified. Rejecting at write time beats storing a
  // contact that silently never gets reached.
  if (!body.email) {
    return NextResponse.json(
      { error: "a contact needs an email address to be notified" },
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
  return NextResponse.json({ contact: data, email_ready: Boolean(row.email) }, { status: 201 });
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
