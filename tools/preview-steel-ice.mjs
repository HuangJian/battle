// Renders a faithful schematic of the new auto-tiled steel & ice terrain using
// the SAME geometry as SpriteArtist.drawSteel / drawIce (sans theme import).
// Output: preview-steel-ice.svg — open it to eyeball the connected-patch look.
import { writeFileSync } from 'node:fs'

const CELL = 48
const modern = {
  steel: '#c9c9c9',
  steelDark: '#9a9a9a',
  ice: '#a9e0f5',
}

function steelCell(x, y, size, n, e, s, w, t) {
  const out = []
  const s4 = size / 4
  out.push(`<rect x="${x}" y="${y}" width="${size}" height="${size}" fill="${t.steel}"/>`)
  // diagonal brushed hatch
  let k = 0
  for (let b = -size; b <= size; b += s4) {
    const col = k % 2 === 0 ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)'
    out.push(
      `<line x1="${x}" y1="${y + b}" x2="${x + size}" y2="${y + size + b}" stroke="${col}" stroke-width="1"/>`,
    )
    k++
  }
  const bevel = 2
  if (!n) {
    out.push(
      `<rect x="${x}" y="${y}" width="${size}" height="${bevel}" fill="rgba(255,255,255,0.22)"/>`,
    )
    out.push(`<rect x="${x}" y="${y}" width="${size}" height="1" fill="${t.steelDark}"/>`)
  }
  if (!s)
    out.push(
      `<rect x="${x}" y="${y + size - bevel}" width="${size}" height="${bevel}" fill="${t.steelDark}"/>`,
    )
  if (!w) {
    out.push(
      `<rect x="${x}" y="${y}" width="${bevel}" height="${size}" fill="rgba(255,255,255,0.22)"/>`,
    )
    out.push(`<rect x="${x}" y="${y}" width="1" height="${size}" fill="${t.steelDark}"/>`)
  }
  if (!e)
    out.push(
      `<rect x="${x + size - bevel}" y="${y}" width="${bevel}" height="${size}" fill="${t.steelDark}"/>`,
    )
  // hinges straddling internal (steel↔steel) seams
  const cx = x + size / 2,
    cy = y + size / 2
  const hinge = (ex, ey, vertical) => {
    const len = size * 0.5,
      thick = 4
    if (vertical)
      out.push(
        `<rect x="${ex - thick / 2}" y="${ey - len / 2}" width="${thick}" height="${len}" fill="${t.steelDark}"/>`,
      )
    else
      out.push(
        `<rect x="${ex - len / 2}" y="${ey - thick / 2}" width="${len}" height="${thick}" fill="${t.steelDark}"/>`,
      )
    out.push(`<circle cx="${ex}" cy="${ey}" r="1.4" fill="rgba(255,255,255,0.3)"/>`)
    out.push(`<circle cx="${ex}" cy="${ey}" r="0.8" fill="rgba(0,0,0,0.35)"/>`)
  }
  if (n) hinge(cx, y, false)
  if (s) hinge(cx, y + size, false)
  if (w) hinge(x, cy, true)
  if (e) hinge(x + size, cy, true)
  // obvious rivets at the four outer corners
  const o = 4.5
  const rivet = (rcx, rcy) => {
    out.push(`<circle cx="${rcx}" cy="${rcy + 0.5}" r="3.2" fill="rgba(0,0,0,0.4)"/>`)
    out.push(`<circle cx="${rcx}" cy="${rcy}" r="2.6" fill="${t.steelDark}"/>`)
    out.push(`<circle cx="${rcx}" cy="${rcy}" r="1.6" fill="rgba(255,255,255,0.25)"/>`)
    out.push(`<circle cx="${rcx - 0.8}" cy="${rcy - 0.8}" r="0.9" fill="rgba(255,255,255,0.85)"/>`)
  }
  if (!n && !w) rivet(x + o, y + o)
  if (!n && !e) rivet(x + size - o, y + o)
  if (!s && !w) rivet(x + o, y + size - o)
  if (!s && !e) rivet(x + size - o, y + size - o)
  return out.join('\n')
}

function iceCell(x, y, size, n, e, s, w) {
  const out = []
  const a = size / 3
  out.push(`<rect x="${x}" y="${y}" width="${size}" height="${size}" fill="#a9e0f5"/>`)
  out.push(
    `<path d="M${x} ${y + a} L${x + a * 2} ${y} M${x} ${y + a * 2} L${x + a} ${y + size} M${x + size} ${y + a} L${x + a * 2} ${y + size} M${x + size} ${y + a * 2} L${x + a} ${y}" stroke="rgba(255,255,255,0.45)" stroke-width="1" fill="none"/>`,
  )
  const f = 2
  if (!n)
    out.push(`<rect x="${x}" y="${y}" width="${size}" height="${f}" fill="rgba(255,255,255,0.4)"/>`)
  if (!s)
    out.push(
      `<rect x="${x}" y="${y + size - f}" width="${size}" height="${f}" fill="rgba(255,255,255,0.4)"/>`,
    )
  if (!w)
    out.push(`<rect x="${x}" y="${y}" width="${f}" height="${size}" fill="rgba(255,255,255,0.4)"/>`)
  if (!e)
    out.push(
      `<rect x="${x + size - f}" y="${y}" width="${f}" height="${size}" fill="rgba(255,255,255,0.4)"/>`,
    )
  out.push(
    `<rect x="${x + size * 0.22}" y="${y + size * 0.28}" width="1" height="1" fill="rgba(255,255,255,0.7)"/>`,
  )
  out.push(
    `<rect x="${x + size * 0.72}" y="${y + size * 0.62}" width="1" height="1" fill="rgba(255,255,255,0.7)"/>`,
  )
  return out.join('\n')
}

// Build a 5x4 steel block and a 4x4 ice block, then single isolated tiles.
const parts = []
const steelMask = (c, r) => c >= 0 && c < 5 && r >= 0 && r < 4
const iceMask = (c, r) => c >= 0 && c < 4 && r >= 0 && r < 4
const inb = (c, r, fn) => c >= 0 && fn(c, r)
function mask(c, r, fn) {
  return [inb(c, r - 1, fn), inb(c + 1, r, fn), inb(c, r + 1, fn), inb(c - 1, r, fn)]
}

let ox = 20,
  oy = 30
for (let r = 0; r < 4; r++)
  for (let c = 0; c < 5; c++) {
    const [nn, ne, ns, nw] = mask(c, r, steelMask)
    parts.push(steelCell(ox + c * CELL, oy + r * CELL, CELL, nn, ne, ns, nw, modern))
  }

ox = 20
oy = 30 + 4 * CELL + 30
for (let r = 0; r < 4; r++)
  for (let c = 0; c < 4; c++) {
    const [nn, ne, ns, nw] = mask(c, r, iceMask)
    parts.push(iceCell(ox + c * CELL, oy + r * CELL, CELL, nn, ne, ns, nw))
  }

// isolated single tiles on the right
ox = 20 + 6 * CELL + 40
parts.push(steelCell(ox, 30 + CELL, CELL, false, false, false, false, modern))
parts.push(iceCell(ox, 30 + 4 * CELL + 30 + CELL, CELL, false, false, false, false))

const W = ox + CELL + 40
const H = oy + 4 * CELL + 40
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<rect width="${W}" height="${H}" fill="#2b2b2b"/>
<text x="20" y="20" fill="#fff" font-family="sans-serif" font-size="14">Connected patches (auto-tiled): outline/rivets only on perimeter</text>
${parts.join('\n')}
</svg>`
writeFileSync(new URL('../preview-steel-ice.svg', import.meta.url), svg)
console.log('wrote preview-steel-ice.svg', W, H)
