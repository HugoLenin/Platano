// Copies the ONE glossary into the web bundle. Runs before dev and build so
// the operator console can never drift from what the agent actually enforces.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "shared", "critical_terms.json");
const dst = join(here, "..", "lib", "critical_terms.json");

if (!existsSync(src)) {
  // A deploy whose build root is `web/` may not have the repo root checked
  // out. The copy already in lib/ is committed, so a missing source is only
  // fatal when there is nothing to fall back to.
  if (existsSync(dst)) {
    console.warn(`[sync-glossary] ${src} not available, keeping the committed copy`);
    process.exit(0);
  }
  console.error(`[sync-glossary] missing ${src} and no committed copy at ${dst}`);
  process.exit(1);
}
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log("[sync-glossary] shared/critical_terms.json -> web/lib/critical_terms.json");
