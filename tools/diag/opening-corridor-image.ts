#!/usr/bin/env bun
/**
 * opening-corridor-image.ts — 把 P1↔P2 连通判据的核验画成一张 PNG。
 *
 * 用途：人工检查「策略认定的通道」到底能不能让 2×2 坦克通过。
 * 默认画 stageId 2020 (Checkers)：左 panel 是地图 + 现存 1 行高的假缝 +
 * 坦克 2×2 footprint 与基地环钢的冲突；右 panel 是策略在放宽长度/砖预算后
 * 唯一几何可行的开挖方案（明确标注：不是默认行为）。
 *
 * Usage:
 *   bun tools/diag/opening-corridor-image.ts
 *   bun tools/diag/opening-corridor-image.ts --stage 2020 --out tmp/g4-open/s20.png
 */
import { createCanvas, GlobalFonts } from '@napi-rs/canvas'
import { World } from '../../src/game/World'
import { decodeStageGrid } from '../../src/nn/config-stage'
import type { Cell } from '../../src/utils/pathfind'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import {
  evaluateCorridor,
  planCorridorDig,
  corridorBands,
  CORRIDOR_WINDOW_ROWS,
  RING_HARD_CELLS,
  HUG_ROW_MIN,
  LOWER_THIRD_ROW,
  CORRIDOR_ROW,
} from '../../src/nn/opening-corridor'
import { DIR_DX, DIR_DY, dirIdx } from '../../src/utils/direction'

const levelPath = 'nn-training/levels/classic35-safe-base.jsonc'
const level = Bun.JSONC.parse(await Bun.file(levelPath).text()) as {
  stages: Array<{ name: string }>
}
const arg = (n: string, d: string): string => {
  const i = process.argv.indexOf(`--${n}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : d
}
const stageId = Number.parseInt(arg('stage', '2020'), 10)
const outPath = arg('out', 'tmp/g4-open/s20-checkers-corridor.png')
const idx = stageId - 2000
const stage = level.stages[idx]
if (!stage) throw new Error(`stageId ${stageId} out of range`)

for (const p of ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/msyhbd.ttc']) {
  try {
    GlobalFonts.registerFromPath(p, 'YaHei')
  } catch {
    /* 已注册或系统无该字体 */
  }
}
const FONT = 'YaHei, sans-serif'

const world = new World()
world.loadStageData(decodeStageGrid(stage as never, stageId), 0)
const tm = world.tileMap
const spawn = world.playerSpawnPoint
const from: Cell = { col: spawn.col, row: spawn.row }
const to: Cell = { col: computePlayer2SpawnCol(spawn.col), row: spawn.row }

const ev = evaluateCorridor(tm, from, to)
const defaultDig = planCorridorDig(tm, from, to, {
  windowLock: null,
  windowRows: CORRIDOR_WINDOW_ROWS,
  maxBricks: 8,
})
const [hugBand, lowerBand] = corridorBands(from, to)

// B panel = 策略默认产出。严格档（hug/lower + 8 砖）无解时自动走放宽档，
// 这就是用户确认「符合预期」的那条下 1/3 走廊。
const strategyPath = defaultDig?.path ?? null
const strategyBricks = defaultDig?.bricks ?? 0
const strategyGeo = defaultDig?.geometry ?? null

/** 路径每一步的坦克中心格。 */
function pathCells(path: readonly string[]): Cell[] {
  const out: Cell[] = [{ ...from }]
  let col = from.col
  let row = from.row
  for (const d of path) {
    const di = dirIdx(d as never)
    col += DIR_DX[di]
    row += DIR_DY[di]
    out.push({ col, row })
  }
  return out
}
/** 路径 footprint 扫到的所有砖 tile（= 要拆的砖）。 */
function dugTiles(path: readonly string[]): Set<string> {
  const out = new Set<string>()
  for (const c of pathCells(path)) {
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        const col = c.col + dc
        const row = c.row + dr
        if (tm.get(col, row) === 'brick') out.add(`${col},${row}`)
      }
    }
  }
  return out
}

const T = 20
const MAP = 26 * T
const LEFT_X = 56
const RIGHT_X = 640
const MAP_Y = 132
const W = 1210
const H = 960
const LEGEND_Y = MAP_Y + MAP + 18

const FILL: Record<string, string> = {
  empty: '#F4F6F8',
  brick: '#E2A36B',
  steel: '#9AA8B8',
  water: '#A9D4EE',
  base: '#F4C430',
  forest: '#BFD8B2',
  ice: '#D8EEF5',
}
const LABEL: Record<string, string> = {
  empty: '',
  brick: 'b',
  steel: 'S',
  water: '~',
  base: 'E',
  forest: 'f',
  ice: 'i',
}

const canvas = createCanvas(W, H)
const ctx = canvas.getContext('2d')
ctx.fillStyle = '#FFFFFF'
ctx.fillRect(0, 0, W, H)
ctx.textBaseline = 'alphabetic'

function text(s: string, x: number, y: number, size = 14, color = '#1F2933', weight = ''): void {
  ctx.fillStyle = color
  ctx.font = `${weight} ${size}px ${FONT}`.trim()
  ctx.fillText(s, x, y)
}

function drawMap(ox: number, title: string): void {
  text(title, ox, MAP_Y - 14, 17, '#111827', 'bold')
  // 地形
  for (let r = 0; r < 26; r++) {
    for (let c = 0; c < 26; c++) {
      const t = tm.get(c, r)
      ctx.fillStyle = FILL[t] ?? '#FFFFFF'
      ctx.fillRect(ox + c * T, MAP_Y + r * T, T, T)
      if (LABEL[t]) {
        ctx.fillStyle = t === 'brick' ? '#8A4B1E' : '#33404D'
        ctx.font = `11px ${FONT}`
        ctx.fillText(LABEL[t], ox + c * T + 6, MAP_Y + r * T + 14)
      }
    }
  }
  // 下 1/3 与 hug band
  ctx.fillStyle = 'rgba(244,196,48,0.10)'
  ctx.fillRect(ox, MAP_Y + LOWER_THIRD_ROW * T, MAP, (26 - LOWER_THIRD_ROW) * T)
  ctx.fillStyle = 'rgba(72,170,120,0.16)'
  ctx.fillRect(ox, MAP_Y + HUG_ROW_MIN * T, MAP, (CORRIDOR_ROW + 1 - HUG_ROW_MIN) * T)
  // 网格
  ctx.strokeStyle = 'rgba(120,132,148,0.28)'
  ctx.lineWidth = 0.5
  for (let i = 0; i <= 26; i++) {
    ctx.beginPath()
    ctx.moveTo(ox + i * T, MAP_Y)
    ctx.lineTo(ox + i * T, MAP_Y + MAP)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(ox, MAP_Y + i * T)
    ctx.lineTo(ox + MAP, MAP_Y + i * T)
    ctx.stroke()
  }
  // 基地环（不可破）
  for (const c of RING_HARD_CELLS) {
    ctx.fillStyle = 'rgba(220,60,60,0.22)'
    ctx.fillRect(ox + c.col * T, MAP_Y + c.row * T, T, T)
    ctx.strokeStyle = '#C0392B'
    ctx.lineWidth = 1.5
    ctx.strokeRect(ox + c.col * T + 0.75, MAP_Y + c.row * T + 0.75, T - 1.5, T - 1.5)
  }
  // 坐标尺
  ctx.fillStyle = '#6B7280'
  ctx.font = `10px ${FONT}`
  for (let r = 0; r < 26; r += 2) ctx.fillText(String(r), ox - 22, MAP_Y + r * T + 14)
  for (let c = 0; c < 26; c += 2) ctx.fillText(String(c), ox + c * T + 4, MAP_Y - 4)
  // 出生点 + 2×2 footprint
  for (const [p, color, name] of [
    [from, '#1B8A4B', 'P1'],
    [to, '#2563C8', 'P2'],
  ] as const) {
    ctx.fillStyle = color
    ctx.fillRect(ox + p.col * T + 4, MAP_Y + p.row * T + 4, T - 8, T - 8)
    ctx.strokeStyle = color
    ctx.lineWidth = 2
    ctx.setLineDash([4, 3])
    ctx.strokeRect(ox + p.col * T + 1, MAP_Y + p.row * T + 1, 2 * T - 2, 2 * T - 2)
    ctx.setLineDash([])
    text(name, ox + p.col * T + 3, MAP_Y + p.row * T - 4, 12, color, 'bold')
  }
}

// ── 标题 ──────────────────────────────────────────────────────────────────
text(
  `P1<->P2 连通判据核验 · ${stage.name} (stageId ${stageId} / index ${idx})`,
  56,
  40,
  22,
  '#111827',
  'bold',
)
text(
  `关卡：nn-training/levels/classic35-safe-base.jsonc  ·  P1(${from.col},${from.row}) -> P2(${to.col},${to.row})  ·  坦克 footprint 2×2 tile`,
  56,
  66,
  13,
  '#4B5563',
)
text(
  `策略判定：connected=${ev.connected} · 默认开挖=${defaultDig ? `${defaultDig.bricks} 砖 / ${defaultDig.geometry.length} 步 / ${defaultDig.relaxed ? '放宽档' : '严格档'}` : 'no_corridor'} · strict 上限 hug ${hugBand.maxLen} / lower ${lowerBand.maxLen} 步`,
  56,
  90,
  13,
  ev.connected ? '#1B8A4B' : '#C0392B',
  'bold',
)

// ── 左 panel：现存缝为什么挤不进去 ───────────────────────────────────────
drawMap(LEFT_X, 'A  现存「通道」：1 行高的缝，坦克 2×2 挤不进去')
{
  const ox = LEFT_X
  // ① 现存缝：row 22, cols 10..15（只有 1 行空）
  ctx.strokeStyle = '#C0392B'
  ctx.lineWidth = 2
  ctx.setLineDash([6, 3])
  ctx.strokeRect(ox + 10 * T + 1, MAP_Y + 22 * T + 1, 6 * T - 2, T - 2)
  ctx.setLineDash([])
  text('①', ox + 10 * T + 2, MAP_Y + 22 * T - 5, 13, '#C0392B', 'bold')
  // ② 坦克中心 (12,22)：footprint = cols 12–13 × rows 22–23
  ctx.strokeStyle = '#B91C1C'
  ctx.lineWidth = 3
  ctx.strokeRect(ox + 12 * T + 1, MAP_Y + 22 * T + 1, 2 * T - 2, 2 * T - 2)
  ctx.fillStyle = 'rgba(185,28,28,0.12)'
  ctx.fillRect(ox + 12 * T + 1, MAP_Y + 22 * T + 1, 2 * T - 2, 2 * T - 2)
  text('②', ox + 12 * T + 3, MAP_Y + 22 * T + 15, 13, '#B91C1C', 'bold')
  // ③ 抬到 row 21 也不行：整排砖（footprint rows 21–22）
  ctx.strokeStyle = '#B45309'
  ctx.lineWidth = 2
  ctx.setLineDash([4, 3])
  ctx.strokeRect(ox + 12 * T + 1, MAP_Y + 21 * T + 1, 2 * T - 2, 2 * T - 2)
  ctx.setLineDash([])
  text('③', ox + 12 * T + 3, MAP_Y + 21 * T - 5, 13, '#B45309', 'bold')
  // 环钢行右侧标记
  text('④', ox + 15 * T + 3, MAP_Y + 23 * T + 15, 13, '#C0392B', 'bold')
  // panel 图例（地图下方，不压地形）
  const legend = [
    ['①', '现存缝：row 22, cols 10–15 只有 1 行空；单格看像通道', '#C0392B'],
    [
      '②',
      '坦克中心放 row 22 -> 2×2 footprint 覆盖 rows 22–23，下沿必压 ④ 基地环钢（不可破）',
      '#B91C1C',
    ],
    ['③', '坦克中心抬到 row 21 -> footprint 覆盖 rows 21–22，整排是砖，仍挤不进去', '#B45309'],
  ]
  let ly = LEGEND_Y
  for (const [n, s, c] of legend) {
    text(n, ox, ly + 12, 13, c, 'bold')
    text(s, ox + 20, ly + 12, 12, '#374151')
    ly += 22
  }
  text(
    '淡黄 = 下 1/3 (rows >=17)　淡绿 = 贴环墙 band (rows 20–22)　红格 = 基地环',
    ox,
    ly + 14,
    11,
    '#6B7280',
  )
}

// ── 右 panel：策略放宽后的唯一几何可行方案 ───────────────────────────────
drawMap(RIGHT_X, 'B  策略实际开出的 P1<->P2 走廊（用户确认符合预期）')
{
  const ox = RIGHT_X
  if (strategyPath && strategyGeo) {
    const dug = dugTiles(strategyPath)
    for (const key of dug) {
      const [c, r] = key.split(',').map(Number)
      ctx.fillStyle = 'rgba(226,163,107,0.95)'
      ctx.fillRect(ox + c * T, MAP_Y + r * T, T, T)
      ctx.strokeStyle = '#B45309'
      ctx.lineWidth = 1.6
      ctx.beginPath()
      ctx.moveTo(ox + c * T + 4, MAP_Y + r * T + 4)
      ctx.lineTo(ox + c * T + T - 4, MAP_Y + r * T + T - 4)
      ctx.moveTo(ox + c * T + T - 4, MAP_Y + r * T + 4)
      ctx.lineTo(ox + c * T + 4, MAP_Y + r * T + T - 4)
      ctx.stroke()
    }
    const cells = pathCells(strategyPath)
    // 每 4 步画一个 footprint 轮廓，直观看到坦克确实占 2×2
    ctx.strokeStyle = 'rgba(37,99,200,0.45)'
    ctx.lineWidth = 1
    for (let i = 0; i < cells.length; i += 4) {
      const c = cells[i]
      ctx.strokeRect(ox + c.col * T + 1, MAP_Y + c.row * T + 1, 2 * T - 2, 2 * T - 2)
    }
    // 中心格路径
    ctx.strokeStyle = '#2563C8'
    ctx.lineWidth = 2.5
    ctx.beginPath()
    cells.forEach((c, i) => {
      const x = ox + c.col * T + T / 2
      const y = MAP_Y + c.row * T + T / 2
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.stroke()
    const legend = [
      `橙色叉 = 拆的砖（${strategyBricks} 块）；蓝线 = 规划的坦克中心格路径（${strategyGeo.length} 步）`,
      `中心最低 row ${strategyGeo.minRow}：仍在下 1/3；贴环墙 hug band 被钢封死后的 lower 兜底`,
      `严格档无解 => 自动放宽（长度 +${4}、砖预算 40）；通路一开就 handoff，无需走到 P2`,
    ]
    let ly = LEGEND_Y
    for (const s of legend) {
      text(s, ox, ly + 12, 12, '#1D4ED8')
      ly += 22
    }
    text(
      '淡蓝虚框 = 每 4 步的 2×2 footprint 抽样（验证坦克真能站进去）',
      ox,
      ly + 14,
      11,
      '#6B7280',
    )
  } else {
    text('放宽后仍无任何几何可行路径', ox + 20, MAP_Y + 10 * T, 16, '#C0392B', 'bold')
  }
}

// ── 底部结论 ──────────────────────────────────────────────────────────────
const by = MAP_Y + MAP + 138
ctx.fillStyle = '#F8FAFC'
ctx.fillRect(56, by - 20, W - 112, H - by + 8)
ctx.strokeStyle = '#E2E8F0'
ctx.lineWidth = 1
ctx.strokeRect(56, by - 20, W - 112, H - by + 8)
const lines = [
  [
    '结论 1',
    '旧口径 A* 对 Checkers 返回 null：2×2 footprint 已正确判定「现存缝挤不进去」。row 22 只有 1 行空，坦克下沿必压 row 23 环钢。',
    '#111827',
  ],
  [
    '结论 2',
    '严格档（贴环墙 + 8 砖）无解 => 策略自动放宽档，开出 28 步 / 35 砖的下 1/3 走廊（B 图）；不破环、单一低位中央窗口。',
    '#111827',
  ],
  [
    '完成条件',
    '通道 OPEN 即 handoff，不再套严格长度门、不要求坦克走到 P2 出生点；开好前逐格校验 2×2 footprint 能站进去。',
    '#1B8A4B',
  ],
  [
    '纠错',
    '上一轮把 index 19「Bastion」的 14 步通道误报成 Checkers。Bastion 与 Checkers 是两张不同的图；低杀 10/14 局全在 Checkers。',
    '#C0392B',
  ],
  [
    '检验标准',
    '「连通」= OPEN 通路 + 每格 2×2 footprint 可容纳坦克 + 下 1/3；任何 1 行高的缝都不算通道。',
    '#1B8A4B',
  ],
]
let y = by
for (const [tag, body, color] of lines) {
  text(`${tag}｜`, 70, y, 13, color, 'bold')
  text(body, 150, y, 13, '#374151')
  y += 26
}

import { writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'
mkdirSync(dirname(outPath), { recursive: true })
writeFileSync(outPath, canvas.toBuffer('image/png'))
console.log(`wrote ${outPath} (${W}x${H})`)
console.log(
  JSON.stringify({
    stageId,
    name: stage.name,
    connected: ev.connected,
    defaultDig: defaultDig
      ? {
          len: defaultDig.geometry.length,
          bricks: defaultDig.bricks,
          minRow: defaultDig.geometry.minRow,
          band: defaultDig.band,
          windowRow: defaultDig.windowRow,
          relaxed: defaultDig.relaxed,
        }
      : 'no_corridor',
  }),
)
