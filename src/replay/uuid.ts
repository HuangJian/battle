// ================================================================
// UUID generator — shared between ReplayManager and file parser.
//
// crypto.randomUUID() requires a secure context (HTTPS). The Vite
// dev server on localhost qualifies, but HTTP LAN access and some
// embedded webviews do not. The Math.random fallback keeps import
// working in all environments.
// ================================================================

/** Generate a UUID (crypto.randomUUID when available, fallback otherwise). */
export function generateUUID(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  let out = ''
  const hex = '0123456789abcdef'
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) out += '-'
    else if (i === 14) out += '4'
    else out += hex[Math.floor(Math.random() * 16)]
  }
  return out
}
