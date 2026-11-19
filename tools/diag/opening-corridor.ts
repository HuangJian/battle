#!/usr/bin/env bun
/**
 * opening-corridor.ts — S0.5b: 35 关 P1↔P2 走廊普查（新地形 / 新判据）。
 *
 * 回答三件事（只读、不跑仿真、不动阈值）：
 *   1. 旧口径「存在开放 A* 通路」认成连通的关，有多少其实是绕北部的长路；
 *   2. 新口径（下 1/3 + 简短直接 + 贴基地环墙）认成连通的关有多少；
 *   3. 没连通的关，用「只在下 1/3 贴环墙开一条 2×2 中央窗口」要拆几块砖，
 *      有没有因为钢/水根本开不出来的关。
 *
 * 关卡来源 = 课程实际使用的 `nn-training/levels/classic35-safe-base.jsonc`
 *（不是 src/config/stages.ts 的 classic35 原版；单层化后的环钢只在这份里）。
 *
 * Usage:
 *   bun tools/diag/opening-corridor.ts
 *   bun tools/diag/opening-corridor.ts --stages 20,8,34
 *   bun tools/diag/opening-corridor.ts --stages all --max-bricks 8
 *   bun tools/diag/opening-corridor.ts --stages all --drive   # 真跑 controller（不跑引擎）
 */
import { World } from '../../src/game/World'
import { decodeStageGrid } from '../../src/nn/config-stage'
import { findPath, type Cell } from '../../src/utils/pathfind'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import { CELL, GRID } from '../../src/constants'
import {
  evaluateCorridor,
  pathGeometry,
  planCorridorDig,
  CORRIDOR_WINDOW_ROWS,
  RING_HARD_CELLS,
  LOWER_THIRD_ROW,
  HUG_ROW_MIN,
} from '../../src/nn/opening-corridor'
import { arg, flag } from '../lib/cli'
import { parseStageSpec, StageSpecError } from '../lib/stage-spec'
import { DIR_DX, DIR_DY, dirIdx } from '../../src/utils/direction'
import { OpeningController, type OpeningAction } from '../../src/nn/opening-controller'

const levelPath = 'nn-training/levels/classic35-safe-base.jsonc'
const level = Bun.JSONC.parse(await Bun.file(levelPath).text()) as { stages: unknown[] }
const spec = arg('stages') ?? 'all'
const maxBricks = Number.parseInt(arg('max-bricks') ?? '8', 10)
let stages: number[]
try {
  stages = parseStageSpec(spec, level.stages.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `opening-corridor: bad --stages: ${spec}`)
  process.exit(1)
}

interface Row {
  idx: number
  naive: string
  naiveNorthern: boolean
  verdict: string
  band: string
  len: number
  minRow: number
  hugRatio: number
  reject: string
  dig: string
  digBricks: number | null
  digWindow: number | null
  digBand: string
  impossible: boolean
}

const rows: Row[] = []
for (const idx of stages) {
  const world = new World()
  const stage = decodeStageGrid(level.stages[idx] as never, 2000 + idx)
  world.loadStageData(stage, 0)
  const tm = world.tileMap
  const spawn = world.playerSpawnPoint
  const from: Cell = { col: spawn.col, row: spawn.row }
  const to: Cell = { col: computePlayer2SpawnCol(spawn.col), row: spawn.row }

  // 旧口径：只 hard-block 基地环的开放 A* 通路。
  const naivePath = findPath(tm, from, to, { hardBlockedCells: RING_HARD_CELLS })
  const naiveGeo = naivePath ? pathGeometry(from, naivePath) : null
  const naiveNorthern = !!naiveGeo && naiveGeo.minRow < LOWER_THIRD_ROW

  const ev = evaluateCorridor(tm, from, to)
  const probe = ev.probe
  const hugReject = ev.hug?.reject ?? (ev.hug ? null : 'no_path')
  const lowerReject = ev.lower?.reject ?? (ev.lower ? null : 'no_path')
  const reject = ev.connected ? '' : `hug:${hugReject ?? '-'}/lower:${lowerReject ?? '-'}`

  const dig = ev.connected
    ? null
    : planCorridorDig(tm, from, to, {
        windowLock: null,
        windowRows: CORRIDOR_WINDOW_ROWS,
        maxBricks,
      })

  rows.push({
    idx,
    naive: naiveGeo ? String(naiveGeo.length) : 'none',
    naiveNorthern,
    verdict: ev.connected ? 'CONN' : 'DIG ',
    band: ev.band ?? '-',
    len: probe?.geometry.length ?? 0,
    minRow: probe?.geometry.minRow ?? 0,
    hugRatio: probe?.geometry.hugRatio ?? 0,
    reject,
    dig: dig ? `${dig.bricks}/${dig.windowRow ?? '-'}/${dig.band}/${dig.geometry.length}` : '-',
    digBricks: dig?.bricks ?? null,
    digWindow: dig?.windowRow ?? null,
    digBand: dig?.band ?? '-',
    impossible: !ev.connected && !dig,
  })
}

console.log(
  `level=${levelPath} stages=${rows.length} maxBricks=${maxBricks} ` +
    `hugRow>=${HUG_ROW_MIN} lowerThird>=${LOWER_THIRD_ROW} grid=${GRID} cell=${CELL}`,
)
console.log(
  'stage  naive  north  verdict band len minRow hug%  reject                 dig(bricks/window/band/len)',
)
for (const r of rows) {
  console.log(
    `S${String(r.idx + 1).padEnd(4)}  ${r.naive.padEnd(5)}  ` +
      `${(r.naiveNorthern ? 'YES' : '-').padEnd(5)}  ` +
      `${r.verdict} ${r.band.padEnd(5)} ${String(r.len).padEnd(3)} ` +
      `${String(r.minRow).padEnd(6)} ${(r.hugRatio * 100).toFixed(0).padStart(3)}  ` +
      `${r.reject.padEnd(22)}  ${r.dig}${r.impossible ? '  << IMPOSSIBLE' : ''}`,
  )
}

const conn = rows.filter((r) => r.verdict === 'CONN')
const naiveOpen = rows.filter((r) => r.naive !== 'none')
const northern = rows.filter((r) => r.naiveNorthern)
const needDig = rows.filter((r) => r.verdict === 'DIG ')
const digPossible = needDig.filter((r) => r.digBricks !== null)
const impossible = needDig.filter((r) => r.impossible)
const brickSum = digPossible.reduce((s, r) => s + (r.digBricks ?? 0), 0)
console.log('')
console.log(
  `naive open ${naiveOpen.length}/${rows.length}; of those northern detour ${northern.length}`,
)
console.log(
  `new criterion connected ${conn.length}/${rows.length} ` +
    `(hug ${conn.filter((r) => r.band === 'hug').length}, lower ${conn.filter((r) => r.band === 'lower').length})`,
)
console.log(
  `need dig ${needDig.length}; legal dig ${digPossible.length} (bricks total ${brickSum}, ` +
    `max ${Math.max(0, ...digPossible.map((r) => r.digBricks ?? 0))}); impossible ${impossible.length}`,
)
if (impossible.length)
  console.log(`impossible stages: ${impossible.map((r) => `S${r.idx + 1}`).join(', ')}`)
console.log(
  `central windows used: ${[...new Set(digPossible.map((r) => r.digWindow ?? '-'))].join(', ')}`,
)

// ── --drive：把 controller 真跑一遍（只应用动作，不跑 Simulation）─────────
// 用来证明判据不是纸上规划：35 张真实关都能收敛到 handoff，且不破基地环。
if (flag('drive')) {
  function applyAction(world: World, action: OpeningAction): void {
    const p = world.player!
    if (!action.move) return
    const di = dirIdx(action.move)
    const cell = { col: Math.round(p.x / CELL), row: Math.round(p.y / CELL) }
    const next = { col: cell.col + DIR_DX[di], row: cell.row + DIR_DY[di] }
    p.dir = action.move
    if (action.fire) {
      let destroyed = false
      for (let dr = 0; dr <= 1 && !destroyed; dr++) {
        for (let dc = 0; dc <= 1 && !destroyed; dc++) {
          if (world.tileMap.get(next.col + dc, next.row + dr) === 'brick') {
            world.tileMap.set(next.col + dc, next.row + dr, 'empty')
            destroyed = true
          }
        }
      }
      if (!destroyed) return
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = 0; dc <= 1; dc++) {
          if (world.tileMap.get(next.col + dc, next.row + dr) === 'brick') return
        }
      }
    }
    p.x = next.col * CELL
    p.y = next.row * CELL
    p.spawnTimer = 0
    world.frame++
  }

  console.log('\n--drive: controller on real stages (no Simulation, deterministic actions)')
  console.log(
    `stage  result        reason      ticks band  len bricksPlanned window relaxed maxTicks exitDone exitTicks atHandoff(P1=8,24/P2=16,24) ringIntact  [exitPocket=${flag('exit-pocket')}]`,
  )
  let connected = 0
  let handedOff = 0
  let ringBreaches = 0
  for (const idx of stages) {
    const world = new World()
    world.loadStageData(decodeStageGrid(level.stages[idx] as never, 2000 + idx), 0)
    const ringSolidBefore = RING_HARD_CELLS.map(
      (c) =>
        world.tileMap.get(c.col, c.row) === 'steel' || world.tileMap.get(c.col, c.row) === 'brick',
    )
    const ctrl = new OpeningController({
      maxCorridorBricks: maxBricks,
      exitPocket: flag('exit-pocket'),
    })
    let step = ctrl.step(world)
    for (let i = 0; i < 3000 && !step.done; i++) {
      applyAction(world, step.action)
      step = ctrl.step(world)
    }
    const snap = ctrl.snapshot()
    const ringIntact = RING_HARD_CELLS.every(
      (c, i) => !ringSolidBefore[i] || world.tileMap.get(c.col, c.row) !== 'empty',
    )
    if (!ringIntact) ringBreaches++
    if (snap.p1p2Connected) connected++
    else handedOff++
    const p = world.player!
    const atHandoff = `(${Math.round(p.x / CELL)},${Math.round(p.y / CELL)})`
    console.log(
      `S${String(idx + 1).padEnd(4)}  ${(snap.p1p2Connected ? 'CONNECTED' : 'HANDOFF  ').padEnd(12)} ` +
        `${(step.abortReason ?? '-').padEnd(11)} ${String(snap.elapsedTicks).padEnd(5)} ` +
        `${(snap.corridorBand ?? '-').padEnd(4)} ${String(snap.corridorLength).padEnd(3)} ` +
        `${String(snap.corridorBricks).padEnd(13)} ${String(snap.centralWindowRow ?? '-').padEnd(6)} ` +
        `${String(snap.corridorRelaxed).padEnd(7)} ${String(snap.openingMaxTicks).padEnd(8)} ` +
        `${String(snap.exitPocketDone).padEnd(8)} ${String(snap.exitPocketTicks).padEnd(9)} ` +
        `${atHandoff.padEnd(9)} ${ringIntact}`,
    )
  }
  console.log(
    `\ndrive summary: p1p2Connected ${connected}/${stages.length}, handoff ${handedOff}, ring breaches ${ringBreaches}`,
  )
}
