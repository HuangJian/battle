// Generates the Battle City Web "Modern Retro" visual asset library as SVG files.
// All sprites use a 96x96 viewBox. Tank sprites face UP so the renderer can
// rotate them per movement direction (same convention as the procedural artist).
import { writeFileSync, mkdirSync } from 'node:fs'

const OUT = 'src/assets/sprites'
mkdirSync(OUT, { recursive: true })

const svg = (inner, defs = '') =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">${defs}${inner}</svg>\n`

// ---------- helpers ----------
const f = (n) => Number(n.toFixed(2))

function starPath(cx, cy, R, r, n = 5, rot = -Math.PI / 2) {
  const pts = []
  for (let i = 0; i < n * 2; i++) {
    const rad = i % 2 === 0 ? R : r
    const a = rot + (i * Math.PI) / n
    pts.push([cx + Math.cos(a) * rad, cy + Math.sin(a) * rad])
  }
  return 'M' + pts.map((p) => `${f(p[0])} ${f(p[1])}`).join(' L') + ' Z'
}

// Two side treads (left 15..30, right 66..81), body spans 30..66.
// High-contrast design: near-black base + bright metallic track links + a
// crisp bright top edge, so the treads clearly separate from the colourful
// hull (the old mid-grey treads blended into the body on some tanks).
function treads() {
  let s = ''
  for (const x of [15, 66]) {
    // Dark casing
    s += `<rect x="${x}" y="20" width="15" height="58" rx="4" fill="#141414"/>`
    s += `<rect x="${x}" y="20" width="15" height="58" rx="4" fill="none" stroke="#000" stroke-width="1"/>`
    // Bright track links (the part that must read as "mechanical tread")
    for (let i = 0; i < 7; i++) {
      const y = 22 + i * 8
      s += `<rect x="${x + 1}" y="${y}" width="13" height="5.5" rx="1.5" fill="#8d8d8d"/>`
      s += `<rect x="${x + 1}" y="${y}" width="13" height="2" rx="1" fill="#d2d2d2"/>`
    }
    // Bright top & dim bottom edges for 3D pop
    s += `<rect x="${x}" y="20" width="15" height="3" rx="1.5" fill="#c6c6c6"/>`
    s += `<rect x="${x}" y="75" width="15" height="3" rx="1.5" fill="#000" opacity="0.55"/>`
  }
  return s
}

// Streamlined treads for the FAST enemy: slanted (chevron) track links that
// lean forward to suggest speed, plus a faint cyan motion streak skimming the
// outer edge of each tread — tying into the enemy's #22C3DC accent colour.
function treadsFast() {
  let s = ''
  for (const x of [15, 66]) {
    s += `<rect x="${x}" y="20" width="15" height="58" rx="4" fill="#141414"/>`
    s += `<rect x="${x}" y="20" width="15" height="58" rx="4" fill="none" stroke="#000" stroke-width="1"/>`
    for (let i = 0; i < 7; i++) {
      const y = 22 + i * 8
      const yb = y + 5.5
      const yt = y + 0.5
      // slanted link (parallelogram leaning right) + bright highlight strip
      s += `<polygon points="${x + 1},${yb} ${x + 13},${yb} ${x + 15},${yt} ${x + 3},${yt}" fill="#8d8d8d"/>`
      s += `<polygon points="${x + 1},${yb} ${x + 13},${yb} ${x + 13},${yb - 2} ${x + 1},${yb - 2}" fill="#d2d2d2" opacity="0.9"/>`
    }
    s += `<rect x="${x}" y="20" width="15" height="3" rx="1.5" fill="#c6c6c6"/>`
    s += `<rect x="${x}" y="75" width="15" height="3" rx="1.5" fill="#000" opacity="0.55"/>`
  }
  // faint cyan speed streaks just outside each tread
  s +=
    `<g opacity="0.5">` +
    `<path d="M12 28 L12 72" stroke="#7FE9F5" stroke-width="2" stroke-linecap="round" stroke-dasharray="3 7"/>` +
    `<path d="M84 28 L84 72" stroke="#7FE9F5" stroke-width="2" stroke-linecap="round" stroke-dasharray="3 7"/>` +
    `</g>`
  return s
}

function shadow() {
  return `<ellipse cx="48" cy="84" rx="32" ry="7" fill="rgba(0,0,0,0.22)"/>`
}

function angrEye(cx, cy, color = '#fff') {
  // white sclera + dark pupil + slanted eyebrow
  return (
    `<ellipse cx="${cx}" cy="${cy}" rx="5.5" ry="6.5" fill="${color}"/>` +
    `<circle cx="${cx + 1}" cy="${cy + 1}" r="2.6" fill="#1a1a1a"/>` +
    `<polygon points="${cx - 7},${cy - 9} ${cx + 7},${cy - 6} ${cx + 7},${cy - 3} ${cx - 7},${cy - 6}" fill="#1a1a1a"/>`
  )
}

// ---------- palette ----------
const P = {
  p1: {
    bodyA: '#FBE08A',
    bodyB: '#F4C430',
    bodyC: '#D9A91E',
    tread: '#2b2b2b',
    turret: '#E8B84B',
    turretDk: '#B9871C',
  },
  p2: {
    bodyA: '#BFE0FF',
    bodyB: '#3D9BF5',
    bodyC: '#2C6FB8',
    tread: '#22303f',
    turret: '#5BA8F0',
    turretDk: '#21508F',
  },
  basic: {
    bodyA: '#F2776A',
    bodyB: '#E23B2C',
    bodyC: '#A8281C',
    tread: '#2b2b2b',
    turret: '#C73023',
    turretDk: '#7E1C14',
  },
  fast: {
    bodyA: '#BDEEF5',
    bodyB: '#22C3DC',
    bodyC: '#148A9C',
    tread: '#103a44',
    turret: '#3FD0E6',
    turretDk: '#0E6E7E',
  },
  power: {
    bodyA: '#C4B0F8',
    bodyB: '#8B5CF6',
    bodyC: '#5B34B0',
    tread: '#2a2140',
    turret: '#A07CF8',
    turretDk: '#46278F',
  },
  armor: {
    bodyA: '#C9D2DA',
    bodyB: '#9AA3AD',
    bodyC: '#6B7480',
    tread: '#3a3f47',
    turret: '#AEB7C0',
    turretDk: '#5b636e',
  },
}

function tankDefs(id, p) {
  return (
    `<linearGradient id="${id}body" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${p.bodyA}"/><stop offset="0.5" stop-color="${p.bodyB}"/><stop offset="1" stop-color="${p.bodyC}"/></linearGradient>` +
    `<linearGradient id="${id}tur" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="${p.turret}"/><stop offset="1" stop-color="${p.turretDk}"/></linearGradient>`
  )
}

// generic rounded body + turret + cannon + emblem/face
function tankBase(id, p, center) {
  return (
    shadow() +
    treads() +
    `<rect x="30" y="26" width="36" height="46" rx="11" fill="url(#${id}body)"/>` +
    `<rect x="34" y="29" width="28" height="6" rx="3" fill="rgba(255,255,255,0.25)"/>` +
    `<rect x="34" y="32" width="28" height="28" rx="7" fill="url(#${id}tur)"/>` +
    `<rect x="44" y="10" width="8" height="24" rx="3" fill="${p.turretDk}"/>` +
    `<rect x="43" y="10" width="10" height="5" rx="2" fill="rgba(0,0,0,0.35)"/>` +
    center
  )
}

// ---------- tank sprites ----------
function playerTank(id, p, starColor) {
  const center = `<path d="${starPath(48, 46, 11, 4.6)}" fill="${starColor}" stroke="${p.bodyC}" stroke-width="1"/>`
  return { defs: tankDefs(id, p), inner: tankBase(id, p, center) }
}

function basicEnemy() {
  const id = 'b'
  const p = P.basic
  const center = angrEye(42, 45) + angrEye(54, 45)
  return { defs: tankDefs(id, p), inner: tankBase(id, p, center) }
}

function fastEnemy() {
  const id = 'f'
  const p = P.fast
  // streamlined low body, side fins, vertical speed streaks (behind, lower), fierce face
  const center =
    // vertical speed streaks behind treads
    `<g opacity="0.5">` +
    `<rect x="20" y="44" width="3" height="30" rx="1.5" fill="#bdeef5"/>` +
    `<rect x="26" y="50" width="2" height="24" rx="1" fill="#bdeef5"/>` +
    `<rect x="68" y="44" width="3" height="30" rx="1.5" fill="#bdeef5"/>` +
    `<rect x="72" y="50" width="2" height="24" rx="1" fill="#bdeef5"/></g>` +
    // side fins
    `<polygon points="30,40 18,52 30,58" fill="${p.bodyC}"/>` +
    `<polygon points="66,40 78,52 66,58" fill="${p.bodyC}"/>` +
    // fierce eyes (angled)
    `<polygon points="38,42 50,46 38,50" fill="#fff"/>` +
    `<polygon points="58,42 46,46 58,50" fill="#fff"/>` +
    `<circle cx="43" cy="46" r="2.4" fill="#10242b"/>` +
    `<circle cx="53" cy="46" r="2.4" fill="#10242b"/>` +
    // fierce brow
    `<polygon points="36,39 52,44 50,41 38,37" fill="#0e3a44"/>` +
    `<polygon points="60,39 44,44 46,41 58,37" fill="#0e3a44"/>` +
    // frown
    `<path d="M42 56 Q48 52 54 56" stroke="#0e3a44" stroke-width="2.4" fill="none" stroke-linecap="round"/>`
  const inner =
    shadow() +
    treadsFast() +
    `<path d="M30 30 Q48 22 66 30 L66 70 Q48 76 30 70 Z" fill="url(#${id}body)"/>` +
    `<rect x="34" y="33" width="28" height="5" rx="2.5" fill="rgba(255,255,255,0.3)"/>` +
    `<rect x="35" y="36" width="26" height="22" rx="7" fill="url(#${id}tur)"/>` +
    `<rect x="44" y="12" width="8" height="22" rx="3" fill="${p.turretDk}"/>` +
    center
  return { defs: tankDefs(id, p), inner }
}

function powerEnemy() {
  const id = 'p'
  const p = P.power
  const center =
    // twin red angry eyes
    `<polygon points="40,44 48,47 40,50" fill="#ff3b30"/>` +
    `<polygon points="56,44 48,47 56,50" fill="#ff3b30"/>` +
    `<circle cx="44" cy="47" r="2" fill="#fff"/>` +
    `<circle cx="52" cy="47" r="2" fill="#fff"/>` +
    `<polygon points="38,41 47,45 45,42 39,39" fill="#3a1f70"/>` +
    `<polygon points="58,41 49,45 51,42 57,39" fill="#3a1f70"/>`
  const inner =
    shadow() +
    treads() +
    // angular octagon body
    `<polygon points="32,28 64,28 68,40 68,62 64,72 32,72 28,62 28,40" fill="url(#${id}body)"/>` +
    `<rect x="34" y="31" width="28" height="5" rx="2" fill="rgba(255,255,255,0.28)"/>` +
    // twin cannons
    `<rect x="40" y="8" width="6" height="22" rx="2.5" fill="${p.turretDk}"/>` +
    `<rect x="50" y="8" width="6" height="22" rx="2.5" fill="${p.turretDk}"/>` +
    `<rect x="39" y="8" width="8" height="4" rx="2" fill="rgba(0,0,0,0.35)"/>` +
    `<rect x="49" y="8" width="8" height="4" rx="2" fill="rgba(0,0,0,0.35)"/>` +
    `<rect x="34" y="38" width="28" height="26" rx="6" fill="url(#${id}tur)"/>` +
    center
  return { defs: tankDefs(id, p), inner }
}

function armorEnemy() {
  const id = 'a'
  const p = P.armor
  const rivet = (cx, cy) =>
    `<circle cx="${cx}" cy="${cy}" r="2.4" fill="#5b636e"/><circle cx="${cx - 0.7}" cy="${cy - 0.7}" r="0.9" fill="#dfe6ec"/>`
  const center =
    // glowing yellow eyes
    `<ellipse cx="42" cy="46" rx="5" ry="6" fill="#FFD23F"/>` +
    `<ellipse cx="54" cy="46" rx="5" ry="6" fill="#FFD23F"/>` +
    `<circle cx="42" cy="46" r="2.2" fill="#7a5200"/>` +
    `<circle cx="54" cy="46" r="2.2" fill="#7a5200"/>` +
    rivet(34, 34) +
    rivet(62, 34) +
    rivet(34, 64) +
    rivet(62, 64)
  const inner =
    shadow() +
    treads() +
    `<rect x="29" y="25" width="38" height="48" rx="8" fill="url(#${id}body)"/>` +
    `<rect x="33" y="28" width="30" height="6" rx="3" fill="rgba(255,255,255,0.25)"/>` +
    `<rect x="34" y="33" width="28" height="28" rx="7" fill="url(#${id}tur)"/>` +
    `<rect x="44" y="9" width="8" height="22" rx="3" fill="${p.turretDk}"/>` +
    center
  return { defs: tankDefs(id, p), inner }
}

// ---------- base (single 3D energy crystal, spans the 2x2 base block) ----------
function baseSprite() {
  const defs =
    `<radialGradient id="cGlow" cx="0.5" cy="0.52" r="0.5"><stop offset="0" stop-color="#9BE7FF" stop-opacity="0.95"/><stop offset="0.55" stop-color="#3FA9F0" stop-opacity="0.45"/><stop offset="1" stop-color="#1E4475" stop-opacity="0"/></radialGradient>` +
    `<linearGradient id="cTop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#EAFBFF"/><stop offset="1" stop-color="#A7E8FF"/></linearGradient>` +
    `<linearGradient id="cFaceL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#BFF0FF"/><stop offset="1" stop-color="#3E9BE0"/></linearGradient>` +
    `<linearGradient id="cFaceR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8FD4F5"/><stop offset="1" stop-color="#2A6FB8"/></linearGradient>` +
    `<linearGradient id="cBotL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5FB0E0"/><stop offset="1" stop-color="#2C6BA8"/></linearGradient>` +
    `<linearGradient id="cBotR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4A93C8"/><stop offset="1" stop-color="#1E4F86"/></linearGradient>`
  const edge = 'stroke="#EAFBFF" stroke-width="1.4" stroke-linejoin="round"'
  const inner =
    // outer energy glow
    `<ellipse cx="48" cy="50" rx="44" ry="44" fill="url(#cGlow)"/>` +
    // energy pad at the foot
    `<ellipse cx="48" cy="84" rx="30" ry="7" fill="rgba(120,200,255,0.35)"/>` +
    // crystal facets (a single faceted gem)
    `<polygon points="48,12 22,46 48,54" fill="url(#cTop)" ${edge}/>` +
    `<polygon points="48,12 74,46 48,54" fill="url(#cTop)" ${edge}/>` +
    `<polygon points="22,46 48,54 30,70" fill="url(#cFaceL)" ${edge}/>` +
    `<polygon points="74,46 66,70 48,54" fill="url(#cFaceR)" ${edge}/>` +
    `<polygon points="48,54 30,70 48,86" fill="url(#cBotL)" ${edge}/>` +
    `<polygon points="48,54 66,70 48,86" fill="url(#cBotR)" ${edge}/>` +
    // bright central ridge highlight
    `<line x1="48" y1="12" x2="48" y2="86" stroke="rgba(255,255,255,0.5)" stroke-width="1.2"/>` +
    // inner core glow
    `<ellipse cx="48" cy="50" rx="9" ry="12" fill="#FFFFFF" opacity="0.55"/>` +
    // sparkles
    `<circle cx="40" cy="30" r="2" fill="#fff"/>` +
    `<circle cx="60" cy="34" r="1.6" fill="#fff"/>` +
    `<circle cx="48" cy="20" r="1.4" fill="#EAFBFF"/>`
  return { defs, inner }
}

// Shattered crystal — drawn once the base is destroyed.
function baseRuinsSprite() {
  const defs = `<linearGradient id="ruL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9AA6B0"/><stop offset="1" stop-color="#5B6670"/></linearGradient>`
  const s = 'stroke="#3A434C" stroke-width="1.2" stroke-linejoin="round"'
  const inner =
    `<ellipse cx="48" cy="84" rx="28" ry="6" fill="rgba(0,0,0,0.3)"/>` +
    `<polygon points="48,20 28,48 44,58" fill="url(#ruL)" ${s}/>` +
    `<polygon points="48,20 68,46 50,56" fill="#7C8893" ${s}/>` +
    `<polygon points="30,64 46,58 40,82 26,76" fill="#5B6670" ${s}/>` +
    `<polygon points="66,62 50,56 52,82 70,76" fill="#46525C" ${s}/>` +
    `<path d="M48 20 L46 40 L52 56 M44 58 L40 70 M50 56 L54 72" stroke="rgba(20,24,28,0.7)" stroke-width="1.4" fill="none"/>` +
    `<polygon points="18,72 26,68 24,80" fill="#6B7681" ${s}/>` +
    `<polygon points="74,74 82,70 80,82" fill="#5B6670" ${s}/>`
  return { defs, inner }
}

// ---------- bullet ----------
function bulletSprite() {
  const defs = `<radialGradient id="bcore" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#fff"/><stop offset="0.6" stop-color="#FFE060"/><stop offset="1" stop-color="#FFB020"/></radialGradient>`
  const inner =
    `<ellipse cx="48" cy="48" rx="9" ry="14" fill="#FFE060" opacity="0.35"/>` +
    `<ellipse cx="48" cy="48" rx="5.5" ry="11" fill="url(#bcore)"/>`
  return { defs, inner }
}

// ---------- terrain (tileable) ----------
function brickSprite() {
  const defs = `<linearGradient id="bk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F2A088"/><stop offset="1" stop-color="#E07A5F"/></linearGradient>`
  let s = `<rect width="96" height="96" fill="#D8C3A8"/>` // mortar
  const bh = 24
  const bw = 48
  for (let row = 0; row < 4; row++) {
    const y = row * bh
    const offset = row % 2 === 0 ? 0 : -bw / 2
    for (let i = -1; i < 3; i++) {
      const x = offset + i * bw
      s += `<rect x="${x + 1.5}" y="${y + 1.5}" width="${bw - 3}" height="${bh - 3}" rx="3" fill="url(#bk)"/>`
      s += `<rect x="${x + 3}" y="${y + 3}" width="${bw - 6}" height="3" rx="1.5" fill="rgba(255,255,255,0.18)"/>`
    }
  }
  return { defs, inner: s }
}

function steelSprite() {
  // Full-bleed metal plate (no per-tile margin) so adjacent steel tiles merge
  // into ONE cohesive wall. A seamless 45° brushed texture (period 24, which
  // divides 96) tiles perfectly, and rivets sit at the four corners so they
  // form a continuous rivet grid across the whole wall.
  const defs =
    `<linearGradient id="st" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="#DDE2E8"/><stop offset="0.5" stop-color="#B3B9C2"/><stop offset="1" stop-color="#9AA1AB"/></linearGradient>`
  // seamless diagonal brushed lines
  let lines = ''
  for (let i = -4; i < 8; i++) {
    const x = i * 24
    lines += `<line x1="${x}" y1="0" x2="${x + 96}" y2="96" stroke="rgba(255,255,255,0.07)" stroke-width="2"/>`
    lines += `<line x1="${x + 12}" y1="0" x2="${x + 108}" y2="96" stroke="rgba(0,0,0,0.06)" stroke-width="2"/>`
  }
  // Rivets use a LIGHT head + DARK outline so they stay visible both on the
  // bright top of the gradient and the darker bottom (a dark rivet vanished
  // against the lower tile — that was the bug).
  const rivet = (cx, cy) =>
    `<circle cx="${cx}" cy="${cy}" r="3.4" fill="#E7EBF0" stroke="#3A3F47" stroke-width="1"/>` +
    `<circle cx="${cx - 0.9}" cy="${cy - 0.9}" r="1.1" fill="#ffffff"/>`
  let s =
    `<rect width="96" height="96" fill="url(#st)"/>` +
    lines +
    // subtle embossed cross seam (low opacity → reads as paneling, not a gap)
    `<rect x="47" y="0" width="2" height="96" fill="rgba(0,0,0,0.09)"/>` +
    `<rect x="0" y="47" width="96" height="2" fill="rgba(0,0,0,0.09)"/>` +
    `<rect x="47.5" y="0" width="1" height="96" fill="rgba(255,255,255,0.12)"/>` +
    `<rect x="0" y="47.5" width="96" height="1" fill="rgba(255,255,255,0.12)"/>`
  for (const [cx, cy] of [
    [9, 9],
    [87, 9],
    [9, 87],
    [87, 87],
  ])
    s += rivet(cx, cy)
  return { defs, inner: s }
}

function waterSprite() {
  const defs = `<linearGradient id="wt" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4FA8E0"/><stop offset="1" stop-color="#2E6FB0"/></linearGradient>`
  let s = `<rect width="96" height="96" fill="url(#wt)"/>`
  for (let row = 0; row < 4; row++) {
    const y = 8 + row * 24
    s +=
      `<path d="M0 ${y} Q12 ${y - 6} 24 ${y} T48 ${y} T72 ${y} T96 ${y}" stroke="rgba(255,255,255,0.35)" stroke-width="3" fill="none"/>` +
      `<path d="M0 ${y + 10} Q12 ${y + 4} 24 ${y + 10} T48 ${y + 10} T72 ${y + 10} T96 ${y + 10}" stroke="rgba(255,255,255,0.18)" stroke-width="2" fill="none"/>`
  }
  return { defs, inner: s }
}

function forestSprite() {
  // Semi-transparent canopy ("若隐若现"): dense enough to read as a leafy bush,
  // but light enough that a tank underneath stays clearly visible. Earlier the
  // canopy was drawn at ~0.8 opacity which fully hid enemies — now dropped to
  // ~0.4–0.5 with looser spacing so the tank's silhouette always peeks through.
  let s = ''
  // dark base canopy — airy coverage
  const base = [
    [30, 30, 26],
    [66, 26, 26],
    [48, 54, 30],
    [22, 64, 22],
    [76, 64, 24],
    [48, 16, 20],
  ]
  for (const [cx, cy, r] of base) {
    s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#2C6E33" opacity="0.5"/>`
  }
  // mid-tone highlights
  const mid = [
    [34, 28, 18],
    [62, 32, 20],
    [50, 56, 22],
    [26, 64, 15],
    [72, 62, 17],
  ]
  for (const [cx, cy, r] of mid) {
    s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#4CA653" opacity="0.42"/>`
  }
  // bright tips
  for (const [cx, cy, r] of [
    [40, 24, 11],
    [58, 42, 13],
    [34, 52, 10],
    [66, 58, 10],
    [48, 70, 11],
  ]) {
    s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#74C879" opacity="0.34"/>`
  }
  return { defs: '', inner: s }
}

function iceSprite() {
  const defs = `<linearGradient id="ic" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#D6F1FB"/><stop offset="1" stop-color="#8FD0E8"/></linearGradient>`
  let s = `<rect width="96" height="96" fill="url(#ic)"/>`
  // crystalline strokes, symmetric for seamless tiling
  s +=
    `<g stroke="rgba(255,255,255,0.6)" stroke-width="2" fill="none">` +
    `<path d="M48 6 L48 90 M6 48 L90 48 M20 20 L76 76 M76 20 L20 76"/>` +
    `<path d="M48 18 L60 30 M48 18 L36 30 M48 78 L60 66 M48 78 L36 66"/>` +
    `<path d="M18 48 L30 60 M18 48 L30 36 M78 48 L66 60 M78 48 L66 36"/></g>` +
    `<rect x="6" y="6" width="10" height="3" rx="1.5" fill="rgba(255,255,255,0.8)"/>` +
    `<rect x="80" y="8" width="3" height="10" rx="1.5" fill="rgba(255,255,255,0.8)"/>`
  return { defs, inner: s }
}

// ---------- items (unified "Modern Retro" power-up badge) ----------
// Every power-up shares ONE visual language so they read as a family:
//   1. a soft golden halo behind,
//   2. a regular (point-up) pentagon GOLD outer border (double-line),
//   3. a dark inner field,
//   4. a distinct icon (star / bomb / plasma-shield / snowflake / tank / helmet),
//   5. the "sparkle" twinkle is animated on top in SpriteArtist.drawPowerUp.
const PENTA_OUTER = 'M48 6 L87.95 35.02 L72.69 81.98 L23.31 81.98 L8.06 35.02 Z'
const PENTA_INNER = 'M48 12 L82.24 36.88 L69.16 77.13 L26.84 77.13 L13.76 36.88 Z'

function itemDefs(id) {
  return (
    // Royal-blue jewel field — contrasts strongly with the warm-cream battlefield
    // and makes both the gold border and the icons (dark bomb / light snowflake) pop.
    `<linearGradient id="${id}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3A5AD8"/><stop offset="0.55" stop-color="#28409E"/><stop offset="1" stop-color="#182668"/></linearGradient>` +
    `<linearGradient id="${id}gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFE9A8"/><stop offset="0.5" stop-color="#F4C430"/><stop offset="1" stop-color="#B8841A"/></linearGradient>` +
    `<radialGradient id="${id}halo" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#FFF3C8" stop-opacity="0.6"/><stop offset="0.6" stop-color="#FFD23F" stop-opacity="0.16"/><stop offset="1" stop-color="#FFD23F" stop-opacity="0"/></radialGradient>`
  )
}
function itemFrame(id) {
  return (
    `<circle cx="48" cy="48" r="46" fill="url(#${id}halo)"/>` +
    `<path d="${PENTA_OUTER}" fill="url(#${id}bg)" stroke="url(#${id}gold)" stroke-width="3" stroke-linejoin="round"/>` +
    // soft top gloss so the medallion reads as glossy, not flat
    `<ellipse cx="48" cy="30" rx="24" ry="11" fill="#FFFFFF" opacity="0.10"/>` +
    `<path d="${PENTA_INNER}" fill="none" stroke="#FFE9A8" stroke-width="0.8" opacity="0.5"/>`
  )
}

function itemStar() {
  const id = 'is'
  const defs = itemDefs(id)
  const inner =
    itemFrame(id) +
    `<path d="${starPath(48, 48, 26, 11)}" fill="url(#${id}gold)" stroke="#FFF3C8" stroke-width="2"/>`
  return { defs, inner }
}
function itemBomb() {
  const id = 'ib'
  const defs =
    itemDefs(id) +
    `<radialGradient id="${id}sph" cx="0.38" cy="0.34" r="0.72"><stop offset="0" stop-color="#8A8A96"/><stop offset="0.5" stop-color="#54545F"/><stop offset="1" stop-color="#26262E"/></radialGradient>`
  const inner =
    itemFrame(id) +
    // metallic graphite sphere with a bright rim so it separates from the blue field
    `<circle cx="48" cy="56" r="22" fill="url(#${id}sph)" stroke="#C6CBD6" stroke-width="1.6"/>` +
    `<circle cx="41" cy="49" r="6.5" fill="rgba(255,255,255,0.4)"/>` +
    `<rect x="46" y="24" width="4" height="14" rx="2" fill="#E8C060" transform="rotate(18 48 31)"/>` +
    `<circle cx="57" cy="23" r="4.8" fill="#FFD23F"/>` +
    `<circle cx="57" cy="23" r="2.2" fill="#fff"/>`
  return { defs, inner }
}
// 护盾道具 → plasma shield (energy dome with plasma filaments)
function itemShield() {
  const id = 'ih'
  const defs =
    itemDefs(id) +
    `<linearGradient id="${id}pl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#CFFBFF"/><stop offset="1" stop-color="#23B6E0"/></linearGradient>`
  const inner =
    itemFrame(id) +
    `<path d="M30 66 Q30 30 48 25 Q66 30 66 66 Z" fill="url(#${id}pl)" opacity="0.95"/>` +
    `<path d="M30 66 Q30 30 48 25 Q66 30 66 66 Z" fill="none" stroke="#EAFDFF" stroke-width="2.6"/>` +
    `<path d="M40 64 L43 52 L39 47 L45 38" fill="none" stroke="#EAFDFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<path d="M56 64 L53 54 L57 49 L52 41" fill="none" stroke="#EAFDFF" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<rect x="27" y="64" width="42" height="3" rx="1.5" fill="#BFF4FF"/>`
  return { defs, inner }
}
// 冰冻道具 → large, bold snowflake (six arms + branches) that nearly fills the
// pentagon. Strokes are drawn THICK + white with a dark outline so the flake
// stays clearly visible when the sprite is shrunk to the 32px tank cell.
function itemFreeze() {
  const id = 'if'
  const defs = itemDefs(id)
  const cx = 48
  const cy = 48
  const R = 27 // arm length — spans almost the full inner pentagon
  let main = ''
  let branch = ''
  // six arms every 60°, starting straight up
  for (const deg of [-90, -30, 30, 90, 150, 210]) {
    const a = (deg * Math.PI) / 180
    const ex = cx + Math.cos(a) * R
    const ey = cy + Math.sin(a) * R
    main += `M${cx} ${cy} L${f(ex)} ${f(ey)} `
    // two pairs of angled branch spurs along each arm (feathered snowflake look)
    for (const tArm of [0.5, 0.78]) {
      const bx = cx + Math.cos(a) * R * tArm
      const by = cy + Math.sin(a) * R * tArm
      const bl = R * 0.3
      for (const off of [55, -55]) {
        const ba = ((deg + off) * Math.PI) / 180
        branch += `M${f(bx)} ${f(by)} L${f(bx + Math.cos(ba) * bl)} ${f(by + Math.sin(ba) * bl)} `
      }
    }
  }
  const grp = (d, w, col) =>
    `<g stroke="${col}" stroke-width="${w}" stroke-linecap="round" fill="none"><path d="${d}"/></g>`
  const inner =
    itemFrame(id) +
    // dark outline underlay so the white flake pops on the blue field
    grp(main + branch, 7.5, '#14245e') +
    grp(main, 4.5, '#F4FBFF') +
    grp(branch, 3, '#DFF3FF') +
    `<circle cx="48" cy="48" r="3.4" fill="#F4FBFF" stroke="#14245e" stroke-width="1.2"/>`
  return { defs, inner }
}
// 加命道具 → little tank (player-yellow, white star emblem)
function itemTank() {
  const id = 'it'
  const defs =
    itemDefs(id) +
    `<linearGradient id="${id}body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FBE08A"/><stop offset="0.5" stop-color="#F4C430"/><stop offset="1" stop-color="#D9A91E"/></linearGradient>`
  const inner =
    itemFrame(id) +
    `<rect x="31" y="42" width="7" height="26" rx="3" fill="#2b2b2b"/>` +
    `<rect x="58" y="42" width="7" height="26" rx="3" fill="#2b2b2b"/>` +
    `<rect x="35" y="44" width="26" height="22" rx="5" fill="url(#${id}body)"/>` +
    `<rect x="35" y="46" width="26" height="3" rx="1.5" fill="rgba(255,255,255,0.3)"/>` +
    `<circle cx="48" cy="46" r="8" fill="#E8B84B" stroke="#B9871C" stroke-width="1"/>` +
    `<rect x="46.5" y="30" width="3" height="14" rx="1.5" fill="#B9871C"/>` +
    `<path d="${starPath(48, 55, 6, 2.4)}" fill="#fff" stroke="#D9A91E" stroke-width="0.6"/>`
  return { defs, inner }
}
// respawn shield → soldier helmet
function itemHelmet() {
  const id = 'ik'
  const defs =
    itemDefs(id) +
    `<linearGradient id="${id}st" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#DDE6F0"/><stop offset="1" stop-color="#8A93A3"/></linearGradient>`
  const inner =
    itemFrame(id) +
    `<path d="M28 52 Q28 30 48 30 Q68 30 68 52 L68 55 Q48 63 28 55 Z" fill="url(#${id}st)" stroke="#E6ECF5" stroke-width="1.5"/>` +
    `<ellipse cx="48" cy="52" rx="21" ry="5" fill="#7c8595"/>` +
    `<path d="M48 32 L48 50" stroke="#E6ECF5" stroke-width="1.5" opacity="0.55"/>` +
    `<path d="M40 40 Q48 34 56 40" fill="none" stroke="#FFFFFF" stroke-width="1.4" opacity="0.5"/>`
  return { defs, inner }
}

// ---------- explosion ----------
function explosionSprite() {
  const defs =
    `<radialGradient id="ex" cx="0.5" cy="0.5" r="0.5">` +
    `<stop offset="0" stop-color="#fff"/><stop offset="0.35" stop-color="#FFE040"/><stop offset="0.7" stop-color="#FF8020"/><stop offset="1" stop-color="#C04020"/></radialGradient>`
  let spikes = ''
  for (let i = 0; i < 12; i++) {
    const a = (i * Math.PI) / 6
    const x1 = 48 + Math.cos(a) * 18
    const y1 = 48 + Math.sin(a) * 18
    const x2 = 48 + Math.cos(a) * 44
    const y2 = 48 + Math.sin(a) * 44
    spikes += `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="#FFB020" stroke-width="5" stroke-linecap="round" opacity="0.85"/>`
  }
  const inner =
    spikes +
    `<circle cx="48" cy="48" r="30" fill="url(#ex)"/>` +
    `<circle cx="48" cy="48" r="14" fill="#fff" opacity="0.9"/>`
  return { defs, inner }
}

// ---------- effect overlays (transparent bg) ----------
// Shield is a transparent energy bubble with a hexagonal guard pattern and an
// edge highlight. It is direction-agnostic (drawn on top of the tank, which
// rotates), so it MUST NOT contain a tank silhouette — an earlier hand-edit
// added a full tank (treads + body + barrel) facing up, which overlaid a
// static "cannon-up" shape on the player every frame. Keep this a pure bubble.
function shieldFx() {
  const defs =
    `<radialGradient id="gradient_0" gradientTransform="matrix(76.1052 0 0 76.1052 36 40)" gradientUnits="userSpaceOnUse" r="0.5" cx="0" cy="0">` +
    `<stop offset="0" stop-color="#BFE9FF" stop-opacity="0.149"/>` +
    `<stop offset="0.7" stop-color="#5AB8F0" stop-opacity="0.2784"/>` +
    `<stop offset="1" stop-color="#2E7DF2" stop-opacity="0.4"/>` +
    `</radialGradient>`
  const inner =
    `<path fill="url(#gradient_0)" fill-opacity="1" transform="matrix(1 0 0 1 12 10)" d="M36 0C60 0 72 18 72 38C72 62 54 76 36 80C18 76 0 62 0 38C0 18 12 0 36 0Z"/>` +
    `<path fill="#BFE9FF" transform="matrix(1 0 0 1 12 10)" d="M70.061 54.6036Q73.25 46.9094 73.25 38Q73.25 30.1721 70.9359 23.2297Q68.5402 16.0429 63.9491 10.6865Q59.0924 5.0204 52.2577 1.9827Q44.984 -1.25 36 -1.25Q27.016 -1.25 19.7423 1.9827Q12.9076 5.0204 8.0509 10.6865Q3.4598 16.0429 1.0641 23.2297Q-1.25 30.1722 -1.25 38Q-1.25 46.9095 1.939 54.6036Q4.8768 61.6915 10.3502 67.3677Q15.401 72.6056 22.193 76.2279Q28.5712 79.6297 35.7289 81.2202L36 81.2805L36.2712 81.2202Q43.4288 79.6296 49.807 76.2279Q56.599 72.6055 61.6498 67.3677Q67.1233 61.6915 70.061 54.6036ZM68.5641 24.0203Q70.75 30.5778 70.75 38Q70.75 46.4119 67.7515 53.6464Q64.9951 60.2969 59.8502 65.6323Q55.0738 70.5857 48.6305 74.0221Q42.6742 77.1988 36 78.7188Q29.3258 77.1988 23.3695 74.0221Q16.9263 70.5857 12.1498 65.6323Q7.005 60.297 4.2485 53.6464Q1.25 46.4119 1.25 38Q1.25 30.5779 3.4359 24.0203Q5.6766 17.298 9.9491 12.3135Q14.4385 7.0758 20.7577 4.2673Q27.5465 1.25 36 1.25Q44.4535 1.25 51.2423 4.2673Q57.5615 7.0758 62.0509 12.3135Q66.3234 17.298 68.5641 24.0203Z" fill-rule="evenodd"/>` +
    `<g opacity="0.7"><path fill="#EAF6FF" transform="matrix(1 0 0 1 18 16)" d="M57.977 45.7277Q60.6 39.3326 60.6 32Q60.6 25.0767 58.6956 19.2207Q56.7422 13.2142 52.9528 8.8563Q49.0042 4.3155 43.3597 1.9166Q37.4384 -0.6 30 -0.6Q22.5616 -0.6 16.6403 1.9166Q10.9958 4.3155 7.0472 8.8563Q3.2577 13.2142 1.3044 19.2207Q-0.6 25.0768 -0.6 32Q-0.6 39.3325 2.023 45.7277Q4.4347 51.6076 8.9373 56.4104Q13.0842 60.8338 18.6877 64.0215Q23.9251 67.001 29.8454 68.5797L30 68.621L30.1546 68.5797Q36.0749 67.001 41.3123 64.0215Q46.9157 60.8338 51.0627 56.4104Q55.5653 51.6077 57.977 45.7277ZM57.5544 19.5918Q59.4 25.2669 59.4 32Q59.4 39.096 56.8668 45.2723Q54.5388 50.948 50.1873 55.5896Q46.1645 59.8806 40.7189 62.9785Q35.6845 65.8425 30 67.3788Q24.3155 65.8425 19.2811 62.9785Q13.8355 59.8806 9.8127 55.5896Q5.4611 50.9479 3.1332 45.2723Q0.6 39.096 0.6 32Q0.6 25.267 2.4456 19.5918Q4.3234 13.8175 7.9528 9.6437Q11.7197 5.3117 17.1097 3.0209Q22.806 0.6 30 0.6Q37.1939 0.6 42.8903 3.0209Q48.2802 5.3117 52.0472 9.6437Q55.6765 13.8174 57.5544 19.5918Z" fill-rule="evenodd"/></g>` +
    `<g opacity="0.5"><clipPath id="clip_0"><rect x="29" y="9" width="38" height="82"/></clipPath><g clip-path="url(#clip_0)"><g opacity="0.5"><path fill="#EAF6FF" transform="matrix(1 0 0 1 30 10)" d="M36.5 10L36.5 9.7058L36.2428 9.5629L18.2428 -0.4371L18 -0.572L-0.2428 9.5629L-0.5 9.7058L-0.5 30L-0.5 30.2942L17.7572 40.4371L18 40.572L36.2428 30.4371L36.5 30.2942L36.5 10ZM35.5 10.2942L18 0.572L0.5 10.2942L0.5 29.7058L18 39.428L35.5 29.7058L35.5 10.2942Z" fill-rule="evenodd"/></g></g><clipPath id="clip_1"><rect x="29" y="9" width="38" height="82"/></clipPath><g clip-path="url(#clip_1)"><g opacity="0.5"><path fill="#EAF6FF" transform="matrix(1 0 0 1 30 50)" d="M36.5 10L36.5 9.7058L36.2428 9.5629L18.2428 -0.4371L18 -0.572L-0.2428 9.5629L-0.5 9.7058L-0.5 30L-0.5 30.2942L17.7572 40.4371L18 40.572L36.2428 30.4371L36.5 30.2942L36.5 10ZM35.5 10.2942L18 0.572L0.5 10.2942L0.5 29.7058L18 39.428L35.5 29.7058L35.5 10.2942Z" fill-rule="evenodd"/></g></g></g>` +
    `<g opacity="0.9"><path fill="#EAF6FF" transform="matrix(1 0 0 1 12 10)" d="M69.83 54.5079Q73 46.8598 73 38Q73 30.2127 70.6987 23.3088Q68.3185 16.1684 63.7592 10.8492Q58.9393 5.2259 52.1561 2.2112Q44.931 -1 36 -1Q27.069 -1 19.8439 2.2112Q13.0607 5.2259 8.2408 10.8492Q3.6814 16.1684 1.3013 23.3088Q-1 30.2127 -1 38Q-1 46.8597 2.17 54.5079Q5.0896 61.5521 10.5302 67.1942Q15.5536 72.4035 22.3107 76.0074Q28.6587 79.393 35.7831 80.9762L36 81.0244L36.2169 80.9762Q43.3413 79.393 49.6893 76.0074Q56.4465 72.4035 61.4698 67.1942Q66.9104 61.5521 69.83 54.5079ZM68.8013 23.9412Q71 30.5372 71 38Q71 46.4617 67.9825 53.7421Q65.2078 60.4364 60.0302 65.8059Q55.2263 70.7877 48.7482 74.2426Q42.7376 77.4483 36 78.9751Q29.2623 77.4483 23.2518 74.2426Q16.7737 70.7877 11.9698 65.8059Q6.7922 60.4364 4.0175 53.7421Q1 46.4617 1 38Q1 30.5373 3.1987 23.9412Q5.4549 17.1725 9.7592 12.1508Q14.2854 6.8702 20.6561 4.0388Q27.4935 1 36 1Q44.5065 1 51.3439 4.0388Q57.7145 6.8702 62.2408 12.1508Q66.545 17.1725 68.8013 23.9412Z" fill-rule="evenodd"/></g>`
  return { defs, inner }
}

function starbuf(n) {
  let stars = ''
  for (let i = 0; i < n; i++) {
    const cx = 48 + (i - (n - 1) / 2) * 16
    stars += `<path d="${starPath(cx, 26, 7, 3)}" fill="#FFE97A" stroke="#FFF3C8" stroke-width="0.8"/>`
  }
  const inner =
    `<ellipse cx="48" cy="48" rx="40" ry="40" fill="none" stroke="rgba(255,225,120,0.35)" stroke-width="3"/>` +
    `<ellipse cx="48" cy="48" rx="40" ry="40" fill="url(#aurl)" opacity="0.12"/>` +
    stars
  const defs = `<radialGradient id="aurl"><stop offset="0" stop-color="#FFE97A"/><stop offset="1" stop-color="#FFE97A" stop-opacity="0"/></radialGradient>`
  return { defs, inner }
}

function hitStage(stage) {
  // Bold, unmistakable battle damage. These overlays sit ON TOP of the enemy's
  // own sprite and rotate with it, so the tank's colour & silhouette stay
  // recognisable — but the damage must now be OBVIOUS (the previous thin dark
  // hairline cracks were nearly invisible). We use glowing hot cracks (orange
  // glow + white-hot core), bright 4-point impact sparks, and an energised
  // outline ring that intensifies per stage.
  const glowCrack = (d) =>
    `<path d="${d}" stroke="rgba(255,150,30,0.85)" stroke-width="5" fill="none" stroke-linecap="round" opacity="0.5"/>` +
    `<path d="${d}" stroke="rgba(255,245,200,0.95)" stroke-width="1.7" fill="none" stroke-linecap="round"/>`
  // 4-point spark starburst — reads instantly as "hit"
  const spark = (cx, cy, r, col = '#fff6c8') =>
    `<path d="M${cx} ${cy - r} L${cx + r * 0.3} ${cy - r * 0.3} L${cx + r} ${cy} L${cx + r * 0.3} ${cy + r * 0.3} L${cx} ${cy + r} L${cx - r * 0.3} ${cy + r * 0.3} L${cx - r} ${cy} L${cx - r * 0.3} ${cy - r * 0.3} Z" fill="${col}"/>` +
    `<circle cx="${cx}" cy="${cy}" r="${r * 0.34}" fill="#fff"/>`
  const ring = (cx, cy, rx, ry, col, w, op) =>
    `<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="none" stroke="${col}" stroke-width="${w}" opacity="${op}"/>`
  const smoke = (cx, cy) =>
    `<circle cx="${cx}" cy="${cy}" r="6" fill="rgba(190,190,190,0.22)"/>` +
    `<circle cx="${cx - 3}" cy="${cy - 4}" r="4" fill="rgba(210,210,210,0.18)"/>`

  if (stage === 0) return { defs: '', inner: '' }
  if (stage === 1) {
    return {
      defs: '',
      inner:
        glowCrack('M42 30 L47 42 L41 54') + glowCrack('M57 34 L52 46 L58 58') + spark(48, 44, 9),
    }
  }
  if (stage === 2) {
    return {
      defs: '',
      inner:
        glowCrack('M42 30 L47 42 L41 54') +
        glowCrack('M57 34 L52 46 L58 58') +
        glowCrack('M36 52 L48 55 L60 51') +
        spark(48, 44, 9) +
        spark(40, 58, 6) +
        ring(48, 48, 34, 41, 'rgba(255,255,255,0.55)', 2, 0.55),
    }
  }
  if (stage === 3) {
    return {
      defs: '',
      inner:
        glowCrack('M42 30 L47 42 L41 54') +
        glowCrack('M57 34 L52 46 L58 58') +
        glowCrack('M36 52 L48 55 L60 51') +
        glowCrack('M45 28 L50 40 L44 52') +
        spark(48, 44, 10) +
        spark(40, 58, 7) +
        spark(60, 40, 6) +
        ring(48, 48, 35, 42, 'rgba(255,120,60,0.9)', 2.4, 0.7) +
        smoke(30, 22) +
        smoke(66, 22),
    }
  }
  // stage 4 — critical: heavy glowing cracks + bold red danger ring + multiple
  // impact sparks + smoke. Unmistakable, yet the tank colour shows through.
  return {
    defs: '',
    inner:
      glowCrack('M42 30 L47 42 L41 54') +
      glowCrack('M57 34 L52 46 L58 58') +
      glowCrack('M36 52 L48 55 L60 51') +
      glowCrack('M45 28 L50 40 L44 52') +
      glowCrack('M30 44 L40 47') +
      glowCrack('M68 44 L58 47') +
      spark(48, 44, 11) +
      spark(40, 58, 8) +
      spark(60, 40, 7) +
      spark(34, 36, 6) +
      ring(48, 48, 36, 43, 'rgba(255,70,50,0.95)', 3, 0.85) +
      ring(48, 48, 31, 37, 'rgba(255,200,120,0.8)', 1.5, 0.7) +
      smoke(30, 20) +
      smoke(66, 22) +
      smoke(48, 18),
  }
}

// ---------- assemble ----------
const files = {
  player1: playerTank('p1', P.p1, '#FFE97A'),
  player2: playerTank('p2', P.p2, '#E6F7FF'),
  enemy_basic: basicEnemy(),
  enemy_fast: fastEnemy(),
  enemy_power: powerEnemy(),
  enemy_armor: armorEnemy(),
  base: baseSprite(),
  base_ruins: baseRuinsSprite(),
  bullet: bulletSprite(),
  brick: brickSprite(),
  steel: steelSprite(),
  water: waterSprite(),
  forest: forestSprite(),
  ice: iceSprite(),
  item_star: itemStar(),
  item_bomb: itemBomb(),
  item_shield: itemShield(),
  item_freeze: itemFreeze(),
  item_tank: itemTank(),
  item_helmet: itemHelmet(),
  explosion: explosionSprite(),
  fx_shield: shieldFx(),
  fx_starbuf1: starbuf(1),
  fx_starbuf2: starbuf(2),
  fx_starbuf3: starbuf(3),
  fx_hit0: hitStage(0),
  fx_hit1: hitStage(1),
  fx_hit2: hitStage(2),
  fx_hit3: hitStage(3),
  fx_hit4: hitStage(4),
}

let count = 0
for (const [name, { defs, inner }] of Object.entries(files)) {
  writeFileSync(`${OUT}/${name}.svg`, svg(inner, defs ? `<defs>${defs}</defs>` : ''))
  count++
}
console.log(`Wrote ${count} SVG sprites to ${OUT}/`)
