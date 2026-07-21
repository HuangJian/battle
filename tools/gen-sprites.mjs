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
function treads() {
  let s = ''
  for (const x of [15, 66]) {
    s += `<rect x="${x}" y="20" width="15" height="58" rx="4" fill="#2b2b2b"/>`
    for (let i = 0; i < 7; i++) {
      s += `<rect x="${x + 1}" y="${22 + i * 8}" width="13" height="4" rx="1" fill="#4a4a4a"/>`
    }
    s += `<rect x="${x}" y="20" width="15" height="3" rx="1.5" fill="#5e5e5e"/>`
  }
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
    treads() +
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

// ---------- base (energy cube) ----------
function baseSprite() {
  const defs =
    `<radialGradient id="bglow" cx="0.5" cy="0.5" r="0.5">` +
    `<stop offset="0" stop-color="#FFE9A8"/><stop offset="1" stop-color="#F4C430"/></radialGradient>` +
    `<linearGradient id="ctop" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFE9A8"/><stop offset="1" stop-color="#F4C430"/></linearGradient>` +
    `<linearGradient id="cleft" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F4C430"/><stop offset="1" stop-color="#C99A12"/></linearGradient>` +
    `<linearGradient id="cright" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#E0B22A"/><stop offset="1" stop-color="#A87E0E"/></linearGradient>`
  const inner =
    `<ellipse cx="48" cy="80" rx="26" ry="6" fill="rgba(0,0,0,0.2)"/>` +
    `<ellipse cx="48" cy="52" rx="34" ry="34" fill="url(#bglow)" opacity="0.35"/>` +
    // isometric cube
    `<polygon points="48,22 74,36 74,64 48,78 22,64 22,36" fill="url(#cleft)"/>` +
    `<polygon points="48,22 74,36 48,50 22,36" fill="url(#ctop)"/>` +
    `<polygon points="74,36 74,64 48,78 48,50" fill="url(#cright)"/>` +
    // inner glow lines
    `<polygon points="48,34 64,43 48,52 32,43" fill="none" stroke="#FFF3C8" stroke-width="2" opacity="0.8"/>` +
    `<circle cx="48" cy="43" r="4" fill="#FFF3C8"/>`
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
  let s = `<rect width="96" height="96" fill="#8A909C"/>`
  const panes = [
    [2, 2],
    [50, 2],
    [2, 50],
    [50, 50],
  ]
  for (const [x, y] of panes) {
    s += `<rect x="${x}" y="${y}" width="44" height="44" rx="5" fill="#C9CDD4"/>`
    s += `<rect x="${x}" y="${y}" width="44" height="8" rx="4" fill="rgba(255,255,255,0.35)"/>`
    s += `<rect x="${x}" y="${y + 36}" width="44" height="8" rx="4" fill="rgba(0,0,0,0.12)"/>`
    // rivets
    for (const [rx, ry] of [
      [x + 6, y + 6],
      [x + 38, y + 6],
      [x + 6, y + 38],
      [x + 38, y + 38],
    ]) {
      s += `<circle cx="${rx}" cy="${ry}" r="2.4" fill="#9aa0ac"/><circle cx="${rx - 0.8}" cy="${ry - 0.8}" r="0.9" fill="#eef1f5"/>`
    }
  }
  return { defs: '', inner: s }
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
  // semi-transparent canopy so tanks peek through ("若隐若现")
  let s = ``
  const blobs = [
    [24, 24, 22],
    [60, 22, 26],
    [40, 44, 28],
    [74, 50, 22],
    [20, 62, 24],
    [56, 70, 26],
  ]
  for (const [cx, cy, r] of blobs) {
    s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#3F9142" opacity="0.82"/>`
    s += `<circle cx="${cx - r * 0.3}" cy="${cy - r * 0.3}" r="${r * 0.45}" fill="#58B35B" opacity="0.85"/>`
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

// ---------- items ----------
function itemStar() {
  const defs = `<linearGradient id="isbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2a30"/><stop offset="1" stop-color="#15151a"/></linearGradient>`
  const inner =
    `<rect x="6" y="6" width="84" height="84" rx="14" fill="url(#isbg)" stroke="#FFD23F" stroke-width="2.5"/>` +
    `<ellipse cx="48" cy="48" rx="30" ry="30" fill="#FFD23F" opacity="0.25"/>` +
    `<path d="${starPath(48, 48, 26, 11)}" fill="#FFD23F" stroke="#FFF3C8" stroke-width="2"/>`
  return { defs, inner }
}
function itemBomb() {
  const defs = `<linearGradient id="ibbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2a30"/><stop offset="1" stop-color="#15151a"/></linearGradient>`
  const inner =
    `<rect x="6" y="6" width="84" height="84" rx="14" fill="url(#ibbg)" stroke="#ff6b5e" stroke-width="2.5"/>` +
    `<circle cx="48" cy="56" r="24" fill="#3a3a42"/>` +
    `<circle cx="40" cy="48" r="7" fill="rgba(255,255,255,0.25)"/>` +
    `<rect x="46" y="22" width="4" height="14" rx="2" fill="#caa15a" transform="rotate(18 48 29)"/>` +
    `<circle cx="58" cy="22" r="4" fill="#FFD23F"/>` +
    `<circle cx="58" cy="22" r="2" fill="#fff"/>`
  return { defs, inner }
}
function itemShield() {
  const defs = `<linearGradient id="ishbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2a30"/><stop offset="1" stop-color="#15151a"/></linearGradient>`
  const inner =
    `<rect x="6" y="6" width="84" height="84" rx="14" fill="url(#ishbg)" stroke="#7FD4FF" stroke-width="2.5"/>` +
    `<path d="M48 22 L70 32 L70 52 Q70 70 48 76 Q26 70 26 52 L26 32 Z" fill="#7FD4FF" opacity="0.85"/>` +
    `<path d="M48 30 L62 38 L62 52 Q62 64 48 69 Q34 64 34 52 L34 38 Z" fill="#E6F7FF"/>`
  return { defs, inner }
}
function itemFreeze() {
  const defs = `<linearGradient id="ifbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a2a30"/><stop offset="1" stop-color="#15151a"/></linearGradient>`
  const inner =
    `<rect x="6" y="6" width="84" height="84" rx="14" fill="url(#ifbg)" stroke="#BFE8F5" stroke-width="2.5"/>` +
    `<g stroke="#BFE8F5" stroke-width="4" stroke-linecap="round">` +
    `<path d="M48 24 V72 M28 36 L68 60 M68 36 L28 60"/></g>` +
    `<g stroke="#E6F7FF" stroke-width="3" stroke-linecap="round">` +
    `<path d="M48 30 V40 M48 56 V66 M32 40 L40 46 M64 40 L56 46 M32 56 L40 52 M64 56 L56 52"/></g>`
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
function shieldFx() {
  const inner =
    `<circle cx="48" cy="48" r="42" fill="none" stroke="rgba(127,212,255,0.7)" stroke-width="3"/>` +
    `<circle cx="48" cy="48" r="38" fill="none" stroke="rgba(255,255,255,0.35)" stroke-width="1.5"/>` +
    `<circle cx="48" cy="48" r="42" fill="rgba(127,212,255,0.08)"/>`
  return { defs: '', inner }
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
  // damage overlays centered ~ (48,48); drawn on top of enemy tank
  const crack = (d, w, op) =>
    `<path d="${d}" stroke="rgba(20,20,20,${op})" stroke-width="${w}" fill="none" stroke-linecap="round"/>`
  const lightCrack = (d, w) =>
    `<path d="${d}" stroke="rgba(255,255,255,0.5)" stroke-width="${w}" fill="none" stroke-linecap="round"/>`
  let inner = ''
  if (stage === 0) {
    inner = `<circle cx="48" cy="48" r="30" fill="none" stroke="rgba(255,255,255,0.0)"/>`
  } else if (stage === 1) {
    inner =
      crack('M40 30 L46 44 L40 56', 2.5, 0.6) +
      lightCrack('M40 30 L46 44 L40 56', 1) +
      crack('M58 34 L52 46 L58 60', 2.5, 0.6)
  } else if (stage === 2) {
    inner =
      `<rect x="30" y="26" width="36" height="46" rx="11" fill="rgba(120,120,120,0.18)"/>` +
      crack('M40 30 L46 44 L40 56', 2.5, 0.65) +
      lightCrack('M40 30 L46 44 L40 56', 1) +
      crack('M58 34 L52 46 L58 60', 2.5, 0.65) +
      crack('M34 50 L48 54 L62 50', 2, 0.55)
  } else if (stage === 3) {
    inner =
      `<rect x="30" y="26" width="36" height="46" rx="11" fill="rgba(90,90,90,0.28)"/>` +
      crack('M40 30 L46 44 L40 56', 3, 0.7) +
      crack('M58 34 L52 46 L58 60', 3, 0.7) +
      crack('M34 50 L48 54 L62 50', 2.5, 0.6) +
      crack('M44 28 L50 40 L44 52', 2, 0.55) +
      `<circle cx="34" cy="36" r="6" fill="rgba(80,80,80,0.5)"/>` +
      `<circle cx="62" cy="60" r="5" fill="rgba(80,80,80,0.5)"/>`
  } else {
    // stage 4 — charred + red glow + sparks
    inner =
      `<rect x="28" y="24" width="40" height="50" rx="11" fill="rgba(40,30,30,0.5)"/>` +
      `<circle cx="48" cy="48" r="30" fill="rgba(200,40,20,0.18)"/>` +
      crack('M40 30 L46 44 L40 56', 3, 0.8) +
      crack('M58 34 L52 46 L58 60', 3, 0.8) +
      crack('M34 50 L48 54 L62 50', 3, 0.75) +
      crack('M44 28 L50 40 L44 52', 2.5, 0.7) +
      `<g stroke="#FFD23F" stroke-width="2" stroke-linecap="round">` +
      `<path d="M48 18 L48 26 M70 40 L62 44 M26 40 L34 44 M48 74 L48 66"/></g>`
  }
  return { defs: '', inner }
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
