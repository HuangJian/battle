/**
 * jsonc.ts — JSONC subset parser for the probe toolchain.
 *
 * Semantics follow the repo's existing readers: strip `//` + block comments
 * (string-aware), drop trailing commas before `]`/`}`, then `JSON.parse`.
 * A syntax error THROWS — never a silent fallback.
 *
 * Why a local copy: the game bundle may not import `dashboard/` (AGENTS §3 —
 * the cross-project import edge is one-way), and `tools/sim/eval-course-ckpt.ts`
 * is a full CLI module. The authoritative reader is python
 * `nn-training/rl/jsonc.py` (the training stack reads the same level files);
 * this keeps the probe generator byte-compatible with it.
 */

/** Remove line and block comments, respecting string literals. */
export function stripJsoncComments(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    const n = text[i + 1]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === '/' && n === '/') {
      while (i < text.length && text[i] !== '\n') i++
      continue
    }
    if (c === '/' && n === '*') {
      i += 2
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++
      i += 2
      continue
    }
    out += c
    i++
  }
  return out
}

/** Drop `,` that is directly followed (whitespace aside) by `]` or `}`. */
export function stripTrailingCommas(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === ',') {
      let j = i + 1
      while (j < text.length && /\s/.test(text[j] ?? '')) j++
      const n = text[j] ?? ''
      if (n === ']' || n === '}') {
        i++
        continue
      }
    }
    out += c
    i++
  }
  return out
}

/** JSONC text → value. Throws on syntax errors (never a partial parse). */
export function parseJsonc(text: string): unknown {
  return JSON.parse(stripTrailingCommas(stripJsoncComments(text)))
}
