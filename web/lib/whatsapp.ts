/**
 * WhatsApp delivery through Kapso (@kapso/whatsapp-cloud-api).
 *
 * Three things drive the design here, all of them WhatsApp platform rules
 * rather than anything about our product:
 *
 * 1. SESSION WINDOW. A business may only send free-form messages inside a
 *    24-hour window opened by an inbound message from that user. Outside it,
 *    only a Meta-approved template may be sent. A trusted contact has never
 *    messaged our number, so the template path is the DEFAULT, not the
 *    fallback. See docs/WHATSAPP_TEMPLATE.md for the exact template we submit.
 *
 * 2. OPT-IN IS THE MITIGATION AND THE CONSENT. When the caller adds a trusted
 *    contact, we ask that contact to send one WhatsApp message to our number.
 *    That single message opens the 24h window AND records real, auditable
 *    consent. It is not a demo trick - it is how this should work in
 *    production. `whatsapp_opt_in_at` is written by the inbound webhook.
 *
 * 3. NATIVE LOCATION. The map goes out as WhatsApp's `location` message type,
 *    which renders an in-app map card. A maps.google.com link is only used
 *    inside the template body, where a second message is not allowed.
 *
 * Serialisation: sends to one recipient are chained so a second message never
 * overlaps the first. Kapso/Meta can reject a concurrent send for the same
 * recipient (409), and a retry storm against a person in an emergency is the
 * worst possible failure mode.
 */

import { WhatsAppClient } from "@kapso/whatsapp-cloud-api";

export interface WaResult {
  ok: boolean;
  channel: "whatsapp";
  messageId?: string;
  error?: string;
  usedTemplate: boolean;
  skipped?: boolean;
}

const SESSION_WINDOW_MS = 24 * 60 * 60 * 1000;

let client: WhatsAppClient | null = null;

export function whatsappConfigured(): boolean {
  return Boolean(
    process.env.WHATSAPP_PHONE_NUMBER_ID &&
      (process.env.KAPSO_API_KEY || process.env.META_ACCESS_TOKEN),
  );
}

function getClient(): WhatsAppClient {
  if (client) return client;
  if (process.env.KAPSO_API_KEY) {
    client = new WhatsAppClient({
      baseUrl: process.env.KAPSO_BASE_URL || "https://app.kapso.ai/api/meta/",
      kapsoApiKey: process.env.KAPSO_API_KEY,
    });
  } else {
    client = new WhatsAppClient({ accessToken: process.env.META_ACCESS_TOKEN! });
  }
  return client;
}

/** True when the contact messaged us recently enough for free-form sends. */
export function sessionWindowOpen(optInAt: string | null | undefined): boolean {
  if (!optInAt) return false;
  const t = Date.parse(optInAt);
  return Number.isFinite(t) && Date.now() - t < SESSION_WINDOW_MS;
}

// --------------------------------------------------------------- send queue
const queues = new Map<string, Promise<unknown>>();

/** Serialise all sends to a given recipient. */
function enqueue<T>(recipient: string, task: () => Promise<T>): Promise<T> {
  const prev = queues.get(recipient) ?? Promise.resolve();
  const next = prev.then(task, task);
  // Keep the chain alive but do not retain rejections.
  queues.set(
    recipient,
    next.catch(() => undefined),
  );
  return next;
}

function statusOf(err: unknown): number | null {
  const msg = err instanceof Error ? err.message : String(err);
  const m = msg.match(/status (\d{3})/);
  return m ? Number(m[1]) : null;
}

const RETRYABLE = new Set([409, 429, 500, 502, 503, 504]);

async function withRetry<T>(label: string, fn: () => Promise<T>, attempts = 4): Promise<T> {
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      const status = statusOf(err);
      if (status !== null && !RETRYABLE.has(status)) throw err;
      // 409 = a message to this recipient is still in flight. 429 = rate
      // limited. Both want the same thing: back off, do not hammer.
      const delay = Math.min(8000, 400 * 2 ** i) + Math.floor(Math.random() * 250);
      console.warn(`[whatsapp] ${label} attempt ${i + 1} failed (${status ?? "?"}), retrying in ${delay}ms`);
      await new Promise((r) => setTimeout(r, delay));
    }
  }
  throw lastErr;
}

// ------------------------------------------------------------------- sends
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
  optInAt?: string | null;
}

/**
 * Body used inside the 24h window. Deliberately mirrors the approved template
 * so a contact sees the same wording either way.
 */
function freeformBody(i: AlertInput): string {
  const es = i.locale.startsWith("es");
  return es
    ? [
        `🚨 ${i.callerName} activó una alerta de emergencia y te registró como contacto de confianza.`,
        ``,
        `Tipo: ${i.emergencyType || "en verificación"}`,
        `Lugar: ${i.location || "en verificación"}`,
        ``,
        `Reporte (enlace temporal): ${i.link}`,
        ``,
        `Este aviso informa, no da instrucciones. No reemplaza a los servicios de emergencia. Si hay peligro, llama al 123.`,
      ].join("\n")
    : [
        `🚨 ${i.callerName} triggered an emergency alert and listed you as a trusted contact.`,
        ``,
        `Type: ${i.emergencyType || "being confirmed"}`,
        `Place: ${i.location || "being confirmed"}`,
        ``,
        `Report (temporary link): ${i.link}`,
        ``,
        `This message informs, it does not instruct. It does not replace emergency services. If there is danger, call 123 / 911 / 112.`,
      ].join("\n");
}

/**
 * Template payload. Variable order MUST match the submitted template exactly:
 *   body {{1}} caller name, {{2}} emergency type, {{3}} location
 *   button url suffix {{1}} signed link token
 */
function templatePayload(i: AlertInput) {
  const langCode = i.locale.startsWith("es") ? "es" : "en";
  const token = i.link.split("/r/")[1] ?? i.link;
  return {
    name: process.env.WHATSAPP_TEMPLATE_NAME || "elb_emergency_alert",
    language: { code: langCode },
    components: [
      {
        type: "body",
        parameters: [
          { type: "text", text: i.callerName || "-" },
          { type: "text", text: i.emergencyType || "-" },
          { type: "text", text: i.location || "-" },
        ],
      },
      {
        type: "button",
        sub_type: "url",
        index: "0",
        parameters: [{ type: "text", text: token }],
      },
    ],
  };
}

export async function sendEmergencyAlert(input: AlertInput): Promise<WaResult> {
  if (!whatsappConfigured()) {
    return { ok: false, channel: "whatsapp", error: "whatsapp not configured", usedTemplate: false, skipped: true };
  }
  if (!input.to) {
    return { ok: false, channel: "whatsapp", error: "no phone number", usedTemplate: false, skipped: true };
  }

  const phoneNumberId = process.env.WHATSAPP_PHONE_NUMBER_ID!;
  const to = input.to.replace(/[^\d]/g, "");
  const inWindow = sessionWindowOpen(input.optInAt);

  return enqueue(to, async () => {
    let usedTemplate = false;
    try {
      const messageId = await withRetry("alert", async () => {
        const api = getClient().messages;
        if (inWindow) {
          const res = await api.sendText({ phoneNumberId, to, body: freeformBody(input) });
          return extractId(res);
        }
        usedTemplate = true;
        const res = await api.sendTemplate({
          phoneNumberId,
          to,
          template: templatePayload(input) as never,
        });
        return extractId(res);
      });

      // Native location card as a follow-up. Only allowed inside the session
      // window - outside it, a second non-template message would be rejected,
      // which is exactly why the template body carries a maps link too.
      if (inWindow && typeof input.lat === "number" && typeof input.lon === "number") {
        try {
          await withRetry("location", () =>
            getClient().messages.sendLocation({
              phoneNumberId,
              to,
              location: {
                latitude: input.lat!,
                longitude: input.lon!,
                name: input.location || "Ubicacion de la emergencia",
                address: input.location || "",
              },
            }),
          );
        } catch (err) {
          console.warn("[whatsapp] location card failed (alert already sent):", err);
        }
      }

      return { ok: true, channel: "whatsapp", messageId, usedTemplate };
    } catch (err) {
      return {
        ok: false,
        channel: "whatsapp",
        error: err instanceof Error ? err.message : String(err),
        usedTemplate,
      };
    }
  });
}

function extractId(res: unknown): string | undefined {
  const r = res as { messages?: Array<{ id?: string }> } | undefined;
  return r?.messages?.[0]?.id;
}

/** Confirmation sent when a contact opts in. Always inside the window. */
export async function sendOptInConfirmation(to: string, locale: string, callerName: string) {
  if (!whatsappConfigured() || !to) return;
  const phoneNumberId = process.env.WHATSAPP_PHONE_NUMBER_ID!;
  const es = locale.startsWith("es");
  const body = es
    ? `✅ Listo. Quedaste registrado como contacto de confianza de ${callerName}.\n\nSolo recibirás un mensaje si esa persona activa una alerta de emergencia. Puedes salir escribiendo BAJA.`
    : `✅ Done. You are now a trusted contact for ${callerName}.\n\nYou will only be messaged if they trigger an emergency alert. Reply STOP to opt out.`;
  await enqueue(to.replace(/[^\d]/g, ""), () =>
    withRetry("optin-confirm", () =>
      getClient().messages.sendText({ phoneNumberId, to: to.replace(/[^\d]/g, ""), body }),
    ),
  ).catch((err) => console.warn("[whatsapp] opt-in confirmation failed:", err));
}
