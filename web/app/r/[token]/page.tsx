/**
 * Signed report viewer.
 *
 * This is the page a relative opens from the emailed alert. It is a server
 * component on purpose: the token is verified and the scope resolved BEFORE
 * any report content exists in the response, so an operator-scope field can
 * never be shipped to a family-scope browser and hidden with CSS.
 *
 * Revocation is checked here rather than in the token, so a link can be killed
 * instantly without rotating the signing secret.
 */

import { notFound } from "next/navigation";
import { supabaseAdmin } from "@/lib/supabase";
import { BadToken, SCOPE_FAMILY, verifyToken, type LinkClaims } from "@/lib/signing";

export const dynamic = "force-dynamic";
export const revalidate = 0;

interface ReportRow {
  id: string;
  operator_txt: string;
  family_txt: string;
  lat: number | null;
  lon: number | null;
  is_final: boolean;
  revoked_at: string | null;
  caller_lang: string | null;
  extraction: Record<string, unknown> | null;
}

function Denied({ title, detail }: { title: string; detail: string }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-6">
      <div className="rounded-xl border border-ink-700 bg-ink-900 p-6 text-center">
        <div className="text-3xl">🔒</div>
        <h1 className="mt-3 text-lg font-bold text-ink-50">{title}</h1>
        <p className="mt-2 text-sm text-ink-400">{detail}</p>
      </div>
    </main>
  );
}

function MapCard({ lat, lon }: { lat: number; lon: number }) {
  const d = 0.004;
  const bbox = [lon - d, lat - d, lon + d, lat + d].map((n) => n.toFixed(6)).join("%2C");
  // OpenStreetMap embed: no API key, so the map works on any deploy without
  // extra setup. The relative gets a map whether or not they have the app.
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat}%2C${lon}`;
  return (
    <section className="overflow-hidden rounded-xl border border-ink-700">
      <iframe
        title="Ubicación de la emergencia"
        src={src}
        className="h-64 w-full border-0 bg-ink-800"
        loading="lazy"
        referrerPolicy="no-referrer"
      />
      <div className="flex items-center justify-between gap-3 bg-ink-900 px-3 py-2">
        <span className="font-mono text-[11px] text-ink-400">
          {lat.toFixed(5)}, {lon.toFixed(5)}
        </span>
        <a
          href={`https://www.google.com/maps?q=${lat},${lon}`}
          target="_blank"
          rel="noreferrer"
          className="rounded bg-ink-700 px-2.5 py-1 text-xs font-semibold text-ink-50 hover:bg-ink-600"
        >
          Abrir en Maps →
        </a>
      </div>
    </section>
  );
}

export default async function ReportPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  const secret = process.env.ELB_REPORT_SIGNING_SECRET ?? "";

  let claims: LinkClaims;
  try {
    claims = await verifyToken(secret, decodeURIComponent(token));
  } catch (err) {
    if (err instanceof BadToken && err.message === "expired") {
      return (
        <Denied
          title="Este enlace expiró"
          detail="Los enlaces de reporte son temporales por seguridad. Pide uno nuevo a la persona que te lo compartió, o contacta a los servicios de emergencia."
        />
      );
    }
    return (
      <Denied
        title="Enlace no válido"
        detail="No pudimos verificar este enlace. Puede estar incompleto o haber sido modificado."
      />
    );
  }

  const sb = supabaseAdmin();
  if (!sb) {
    return <Denied title="Servicio no disponible" detail="El almacenamiento no está configurado." />;
  }

  const { data } = await sb
    .from("reports")
    .select("id,operator_txt,family_txt,lat,lon,is_final,revoked_at,caller_lang,extraction")
    .eq("id", claims.reportId)
    .maybeSingle<ReportRow>();

  if (!data) notFound();

  if (data.revoked_at) {
    return (
      <Denied
        title="Este enlace fue revocado"
        detail="El acceso a este reporte fue cancelado por la persona afectada o por el operador."
      />
    );
  }

  // Audit the open. Best-effort: never block the reader on a logging failure.
  const { data: link } = await sb
    .from("report_links")
    .select("id,open_count,revoked_at")
    .eq("report_id", claims.reportId)
    .eq("contact_id", claims.contactId)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<{ id: number; open_count: number; revoked_at: string | null }>();

  if (link?.revoked_at) {
    return (
      <Denied
        title="Este enlace fue revocado"
        detail="El acceso a este reporte fue cancelado. Si necesitas información, contacta a los servicios de emergencia."
      />
    );
  }
  if (link) {
    await sb
      .from("report_links")
      .update({ opened_at: new Date().toISOString(), open_count: (link.open_count ?? 0) + 1 })
      .eq("id", link.id);
  }

  // SCOPE IS RESOLVED HERE. The other scope's text is never read into the
  // response body, so there is nothing for a client-side bug to reveal.
  const isFamily = claims.scope === SCOPE_FAMILY;
  const body = isFamily ? data.family_txt : data.operator_txt;
  const lang = data.caller_lang ?? "es";
  const expires = new Date(claims.exp * 1000);

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <header className="flex flex-wrap items-center gap-3">
        <span className="rounded bg-crit-600 px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-white">
          {isFamily ? "Contacto de confianza" : "Uso profesional"}
        </span>
        {!data.is_final ? (
          <span className="rounded bg-warn-900 px-2 py-1 text-[11px] font-bold text-warn-400">
            Aviso preliminar · la llamada sigue en curso
          </span>
        ) : (
          <span className="rounded bg-ink-800 px-2 py-1 text-[11px] font-bold text-ink-400">
            Reporte final
          </span>
        )}
        <span className="ml-auto text-[11px] text-ink-600">
          Enlace válido hasta {expires.toLocaleString("es-CO")}
        </span>
      </header>

      <h1 className="mt-4 text-2xl font-bold tracking-tight">
        Reporte de emergencia
      </h1>
      <p className="mt-1 font-mono text-xs text-ink-600">{data.id}</p>

      {isFamily ? (
        <div className="mt-4 rounded-xl border border-info-400/30 bg-ink-900 p-4 text-sm text-ink-200">
          <p>
            Recibes esto porque fuiste registrado como <b>contacto de confianza</b>. Se
            omiten los detalles clínicos, que quedan únicamente con el personal de
            emergencia.
          </p>
        </div>
      ) : null}

      {data.lat != null && data.lon != null ? (
        <div className="mt-5">
          <MapCard lat={data.lat} lon={data.lon} />
        </div>
      ) : null}

      <section className="mt-5 overflow-hidden rounded-xl border border-ink-700 bg-ink-900">
        <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-ink-400">
            Reporte (.txt)
          </span>
          <a
            href={`/api/report/${encodeURIComponent(token)}`}
            download
            className="rounded bg-ink-700 px-2.5 py-1 text-xs font-semibold hover:bg-ink-600"
          >
            Descargar
          </a>
        </div>
        <pre className="scroll-thin overflow-x-auto px-4 py-4 font-mono text-[11px] leading-relaxed text-ink-200">
{body}
        </pre>
      </section>

      <div className="mt-5">
      </div>

      <p className="mt-4 text-center text-[11px] text-ink-600">
        Este enlace es personal y temporal. No lo reenvíes.
      </p>
    </main>
  );
}
