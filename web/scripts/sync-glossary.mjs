// Copies the ONE glossary into the web bundle. Runs before dev and build so
// the operator console can never drift from what the agent actually enforces.
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "shared", "critical_terms.json");
const dst = join(here, "..", "lib", "critical_terms.json");

if (!existsSync(src)) {
  console.error(`[sync-glossary] missing ${src}`);
  process.exit(1);
}
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log("[sync-glossary] shared/critical_terms.json -> web/lib/critical_terms.json");
