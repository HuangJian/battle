/**
 * Auto-discovery of the latest NN weights file (plan: no manual rename on restore).
 *
 * Convention (see nn-training/WEIGHTS.md):
 *   - Versioned archive: `weights.<YYYYMMDD-HHMMSS>_ep<N>_val<V>.json`
 *   - Active pointer:     `weights.json` (exact copy of the latest archive)
 *
 * `resolveLatestWeights(dir)` returns the newest versioned file by its embedded
 * timestamp, falling back to `weights.json` when no versioned file exists.
 * This is what the TS runtime (`src/nn/infer.ts`) calls to load weights after a
 * restore from netdisk — the user never has to rename a file.
 */
import { readdirSync, existsSync } from "fs";
import { join } from "path";

const VERSIONED_RE = /^weights\.(\d{8}-\d{6})_ep\d+_val[\d.]+?\.json$/;

function stampOf(name: string): string | null {
  const m = VERSIONED_RE.exec(name);
  return m ? m[1] : null;
}

export function resolveLatestWeights(dir: string): string | null {
  if (!existsSync(dir)) return null;
  const versioned: Array<[string, string]> = [];
  for (const name of readdirSync(dir)) {
    const ts = stampOf(name);
    if (ts) versioned.push([ts, name]);
  }
  if (versioned.length > 0) {
    versioned.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    const newest = versioned[versioned.length - 1][1];
    return join(dir, newest);
  }
  const pointer = join(dir, "weights.json");
  return existsSync(pointer) ? pointer : null;
}
