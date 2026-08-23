/**
 * SMTP credential check. Answers one question before a demo depends on it:
 * will this account actually authenticate, and can it reach a real inbox?
 *
 *   node scripts/check-email.mjs                  # verify credentials only
 *   node scripts/check-email.mjs you@example.com  # verify, then send a test
 *
 * Exits non-zero on failure so it can gate a smoke test. Reads web/.env.local
 * directly rather than relying on the Next.js runtime, so it works standalone.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import nodemailer from "nodemailer";

const here = dirname(fileURLToPath(import.meta.url));

/** Minimal .env parser. Handles quotes and inline comments, ignores the rest. */
function loadEnv(path) {
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    console.error(`x cannot read ${path}`);
    process.exit(1);
  }
  const out = {};
  for (const line of raw.split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)$/i);
    if (!m) continue;
    let v = m[2].trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    out[m[1]] = v;
  }
  return out;
}

const env = loadEnv(resolve(here, "..", ".env.local"));
const user = env.GMAIL_USER;
const pass = (env.GMAIL_APP_PASSWORD || "").replace(/\s+/g, "");
const host = env.SMTP_HOST || "smtp.gmail.com";
const port = Number(env.SMTP_PORT || 465);

if (!user || !pass) {
  console.error("x GMAIL_USER and GMAIL_APP_PASSWORD must both be set in web/.env.local");
  process.exit(1);
}

console.log(`  host : ${host}:${port}`);
console.log(`  user : ${user}`);
console.log(`  pass : ${pass.length} chars (Google App Passwords are 16)`);
if (pass.length !== 16) {
  console.warn("  ! that is not 16 characters - Google App Passwords always are");
}
console.log("");

const transport = nodemailer.createTransport({
  host,
  port,
  secure: port === 465,
  auth: { user, pass },
  connectionTimeout: 15_000,
  greetingTimeout: 15_000,
});

/** Map the opaque SMTP failures onto the thing you actually have to go fix. */
function explain(err) {
  const msg = String(err?.message || err);
  const code = err?.responseCode;
  if (code === 535 || /Username and Password not accepted/i.test(msg)) {
    return [
      "Authentication was rejected. The usual causes, most likely first:",
      `  1. ${user} is not a Google account. Gmail SMTP only authenticates`,
      "     Google accounts - a plain @gmail.com address, or a domain on",
      "     Google Workspace. A domain you merely own will always fail here.",
      "  2. The value is the account password, not a 16-character App Password.",
      "  3. 2-Step Verification is off, so the App Password is not valid.",
      "  4. A Workspace admin disabled App Passwords for the organisation.",
    ].join("\n");
  }
  if (code === 534 || /application-specific password/i.test(msg)) {
    return "This account requires an App Password. Generate one at myaccount.google.com/apppasswords";
  }
  if (/ETIMEDOUT|ECONNREFUSED/i.test(msg) || err?.code === "ETIMEDOUT") {
    return `Could not reach ${host}:${port}. A firewall or ISP is probably blocking outbound SMTP. Try SMTP_PORT=587.`;
  }
  if (/getaddrinfo|EDNS|ENOTFOUND/i.test(msg)) {
    return `DNS could not resolve ${host}. Check the hostname and your connection.`;
  }
  return msg;
}

try {
  await transport.verify();
  console.log("OK  credentials accepted, transport is ready");
} catch (err) {
  console.error("x   SMTP verify failed\n");
  console.error(explain(err));
  console.error(`\nraw: ${err?.responseCode ?? err?.code ?? "?"} ${String(err?.message || err)}`);
  process.exit(1);
}

const to = process.argv[2];
if (!to) {
  console.log("\nPass a recipient to send a real test message:");
  console.log("  node scripts/check-email.mjs you@example.com");
  process.exit(0);
}

try {
  const info = await transport.sendMail({
    from: env.MAIL_FROM || user,
    to,
    subject: "AuXio - prueba de envio",
    text: [
      "Esta es una prueba del canal de correo de AuXio.",
      "",
      "Si la recibiste, el envio de alertas de emergencia funciona.",
      "Revisa tambien Spam y Promociones: la primera vez suele caer ahi.",
    ].join("\n"),
  });
  console.log(`OK  test message sent to ${to}`);
  console.log(`    id: ${info.messageId}`);
  if (info.rejected?.length) console.warn(`    ! rejected: ${info.rejected.join(", ")}`);
  console.log("\nCheck the inbox AND the spam folder.");
} catch (err) {
  console.error(`x   send to ${to} failed\n`);
  console.error(explain(err));
  process.exit(1);
}
