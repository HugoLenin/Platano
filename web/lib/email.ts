/**
 * Email delivery over SMTP - the only outbound channel this deployment has.
 *
 * Defaults to Gmail SMTP because it is the one transport that works with an
 * account you already own: a 16-character App Password and nothing else. No
 * domain to verify, no sender reputation to warm up, no API key to provision.
 * Host and port stay overridable (SMTP_HOST / SMTP_PORT) so that swapping in a
 * real transactional provider later is a config change, not a code change.
 *
 * Three things about this file are deliberate, and all three exist because the
 * recipient is a frightened relative rather than a marketing list:
 *
 * 1. EVERY MESSAGE IS ALSO PLAIN TEXT. Gmail SMTP sending HTML-only to an
 *    unfamiliar recipient is a reliable way to land in Promotions or Spam. A
 *    `text` alternative is the single cheapest deliverability win available,
 *    and it is also what a smartwatch or a screen reader will actually read.
 *
 * 2. THE LOCATION IS A LINK, NOT A MAP. WhatsApp had a native location card;
 *    email has nothing equivalent, so coordinates go out as a maps.google.com
 *    URL. Losing the inline map is the real cost of dropping WhatsApp, and it
 *    is worth stating rather than hiding.
 *
 * 3. SENDS TO ONE RECIPIENT ARE SERIALISED. Gmail throttles aggressively and
 *    answers a burst with a temporary block on the whole account. A retry storm
 *    aimed at someone in an emergency is the worst possible failure mode, so
 *    the queue below is a safety feature, not an optimisation.
 */

import nodemailer, { type Transporter } from "nodemailer";

export interface EmailResult {
  ok: boolean;
  channel: "email";
  messageId?: string;
  error?: string;
  skipped?: boolean;
}

export function emailConfigured(): boolean {
  return Boolean(process.env.GMAIL_USER && process.env.GMAIL_APP_PASSWORD);
}

let transporter: Transporter | null = null;

function getTransport(): Transporter {
  if (transporter) return transporter;
  // Port 465 with implicit TLS rather than 587 with STARTTLS: on Windows and
  // on several cloud hosts, 587 is the port most likely to be filtered, and a
  // blocked STARTTLS upgrade fails with a timeout that looks like a hang.
  const port = Number(process.env.SMTP_PORT || 465);
  transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST || "smtp.gmail.com",
    port,
    secure: port === 465,
    auth: {
      user: process.env.GMAIL_USER!,
      // App Passwords are shown to humans in groups of four. People paste them
      // with the spaces in, and Google rejects them that way, so strip.
      pass: (process.env.GMAIL_APP_PASSWORD || "").replace(/\s+/g, ""),
    },
    pool: true,
    maxConnections: 2,
    // A relative is waiting. Better to fail fast and log than to hold the
    // agent's notify call open while SMTP stalls.
    connectionTimeout: 10_000,
    greetingTimeout: 10_000,
    socketTimeout: 20_000,
  });
  return transporter;
}

/** Verify credentials without sending. Used by scripts/check_email.mjs. */
export async function verifyTransport(): Promise<void> {
  await getTransport().verify();
}

// --------------------------------------------------------------- send queue
const queues = new Map<string, Promise<unknown>>();

function enqueue<T>(recipient: string, task: () => Promise<T>): Promise<T> {
  const prev = queues.get(recipient) ?? Promise.resolve();
  const next = prev.then(task, task);
  queues.set(
    recipient,
    next.catch(() => undefined),
  );
  return next;
}

/**
 * SMTP splits failures into permanent (5xx) and temporary (4xx). Retrying a
 * 5xx is pointless - a bad password stays bad - and retrying it against Gmail
 * counts toward the failed-auth limit that gets an account locked.
 */
function isRetryable(err: unknown): boolean {
  const e = err as { responseCode?: number; code?: string };
  if (typeof e?.responseCode === "number") return e.responseCode >= 400 && e.responseCode < 500;
  return ["ETIMEDOUT", "ECONNRESET", "ESOCKET", "ECONNECTION", "EDNS"].includes(e?.code ?? "");
}

async function withRetry<T>(label: string, fn: () => Promise<T>, attempts = 3): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      if (!isRetryable(err) || i === attempts - 1) throw err;
      const delay = Math.min(8000, 500 * 2 ** i);
      console.warn(`[email] ${label} attempt ${i + 1} failed, retrying in ${delay}ms`);
      await new Promise((r) => setTimeout(r, delay));
    }
  }
  throw lastErr;
}

// ------------------------------------------------------------------- render
export interface AlertInput {
  to: string;
  contactName: string;
  callerName: string;
  emergencyType: string;
  location: string;
  locale: string;
  link: string;
  lat?: number | null;
  lon?: number | null;
}

function mapsUrl(lat?: number | null, lon?: number | null): string | null {
  if (typeof lat !== "number" || typeof lon !== "number") return null;
  return `https://maps.google.com/?q=${lat},${lon}`;
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function esc(s: string): string {
  // Contact and caller names reach this from user input and land in an HTML
  // document. Escaping here rather than trusting the source.
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c] ?? c);
}

function subjectFor(i: AlertInput): string {
  const es = i.locale.startsWith("es");
  const who = i.callerName || (es ? "un contacto" : "a contact");
  return es ? `Alerta de emergencia - ${who}` : `Emergency alert - ${who}`;
}

/**
 * Plain-text body. Mirrors the HTML exactly: a recipient whose client blocks
 * HTML must not get a worse version of an emergency alert.
 */
function textBody(i: AlertInput): string {
  const es = i.locale.startsWith("es");
  const map = mapsUrl(i.lat, i.lon);
  const unknown = es ? "en verificacion" : "being confirmed";
  const lines = es
    ? [
        `ALERTA DE EMERGENCIA`,
        ``,
        `${i.callerName || "Un contacto"} activo una alerta de emergencia y te`,
        `registro como contacto de confianza.`,
        ``,
        `Tipo:  ${i.emergencyType || unknown}`,
        `Lugar: ${i.location || unknown}`,
        ...(map ? [`Mapa:  ${map}`] : []),
        ``,
        `Ver el reporte (enlace temporal):`,
        i.link,
        ``,
        `Este aviso informa, no da instrucciones. No reemplaza a los servicios`,
        `de emergencia. Si hay peligro, llama al 123.`,
      ]
    : [
        `EMERGENCY ALERT`,
        ``,
        `${i.callerName || "A contact"} triggered an emergency alert and listed`,
        `you as a trusted contact.`,
        ``,
        `Type:  ${i.emergencyType || unknown}`,
        `Place: ${i.location || unknown}`,
        ...(map ? [`Map:   ${map}`] : []),
        ``,
        `View the report (temporary link):`,
        i.link,
        ``,
        `This message informs, it does not instruct. It does not replace`,
        `emergency services. If there is danger, call 123 / 911 / 112.`,
      ];
  return lines.join("\n");
}

function htmlBody(i: AlertInput): string {
  const es = i.locale.startsWith("es");
  const map = mapsUrl(i.lat, i.lon);
  const unknown = es ? "en verificación" : "being confirmed";
  const t = es
    ? {
        title: "Alerta de emergencia",
        lead: `<b>${esc(i.callerName || "Un contacto")}</b> activó una alerta y te registró como contacto de confianza.`,
        type: "Tipo",
        place: "Lugar",
        map: "Ver en el mapa",
        cta: "Ver reporte",
        note: "Este aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia profesionales. Si hay peligro, llama al 123.",
      }
    : {
        title: "Emergency alert",
        lead: `<b>${esc(i.callerName || "A contact")}</b> triggered an alert and listed you as a trusted contact.`,
        type: "Type",
        place: "Place",
        map: "Open in maps",
        cta: "View report",
        note: "This message informs, it does not instruct. It does not replace professional emergency services. If there is danger, call 123 / 911 / 112.",
      };

  // Table-free, inline-styled, one column. Not for elegance - Gmail strips
  // <style> blocks and Outlook ignores flexbox, and this has to render on a
  // phone lock screen preview.
  return `<!doctype html>
<html lang="${es ? "es" : "en"}"><body style="margin:0;padding:24px;background:#f5f5f4">
  <div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:560px;
              margin:0 auto;background:#fff;border-radius:12px;padding:24px;
              border-top:4px solid #b91c1c">
    <h2 style="margin:0 0 12px;color:#b91c1c;font-size:20px">&#128680; ${t.title}</h2>
    <p style="margin:0 0 16px;font-size:15px;line-height:1.5;color:#1c1917">${t.lead}</p>
    <table style="border-collapse:collapse;margin:0 0 20px;font-size:15px">
      <tr><td style="padding:2px 12px 2px 0;color:#57534e">${t.type}:</td>
          <td style="padding:2px 0;color:#1c1917"><b>${esc(i.emergencyType || unknown)}</b></td></tr>
      <tr><td style="padding:2px 12px 2px 0;color:#57534e">${t.place}:</td>
          <td style="padding:2px 0;color:#1c1917"><b>${esc(i.location || unknown)}</b></td></tr>
    </table>
    <p style="margin:0 0 20px">
      <a href="${i.link}" style="background:#b91c1c;color:#fff;padding:12px 20px;
         border-radius:8px;text-decoration:none;font-weight:600;display:inline-block"
         >${t.cta}</a>
    </p>
    ${map ? `<p style="margin:0 0 20px;font-size:14px"><a href="${map}" style="color:#b91c1c">&#128205; ${t.map}</a></p>` : ""}
    <p style="margin:0;font-size:12px;line-height:1.5;color:#78716c;
              border-top:1px solid #e7e5e4;padding-top:14px">${t.note}</p>
  </div>
</body></html>`;
}

// -------------------------------------------------------------------- send
export async function sendEmergencyAlert(input: AlertInput): Promise<EmailResult> {
  if (!emailConfigured()) {
    return { ok: false, channel: "email", error: "email not configured", skipped: true };
  }
  if (!input.to) {
    return { ok: false, channel: "email", error: "contact has no email address", skipped: true };
  }

  return enqueue(input.to, async () => {
    try {
      const info = await withRetry("alert", () =>
        getTransport().sendMail({
          from: process.env.MAIL_FROM || process.env.GMAIL_USER!,
          to: input.to,
          subject: subjectFor(input),
          text: textBody(input),
          html: htmlBody(input),
          headers: {
            "X-Priority": "1",
            Importance: "high",
          },
        }),
      );
      return { ok: true, channel: "email", messageId: info.messageId };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[email] send to ${input.to} failed: ${msg}`);
      return { ok: false, channel: "email", error: msg };
    }
  });
}
