#!/usr/bin/env bun
/**
 * pursuit-tail-scenes.ts — §302 "并道射击" 场景扫描器（供人工审查用）。
 *
 * 净胜率是噪声，但"改的对不对"必须先回答一个更基本的问题：**这个机制到底做出
 * 了什么动作**。本工具跑 candidate 臂（默认 mode 3），逐 tick 检测
 *
 *     横向并道（_pursuitTailOverrides 连续触发）
 *       → 抵达目标车道（lane gap 归零）
 *       → 沿车道对齐开火 / 命中击杀
 *
 * 把每一次这样的完整事件簇打成一个 scene，按"时长 + 车道内射击数 + 击杀"排序，
 * 输出 stage@seed、tick 区间（含换算秒数）、玩家与目标的行进轨迹，供人工看录像核对。
 *
 * 用法：
 *   bun tools/diag/pursuit-tail-scenes.ts --mode 3 --top 12
 *   bun tools/diag/pursuit-tail-scenes.ts --stages 32,26 --seeds 1-20 --mode 3 --top 6
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { RNG } from '../../src/utils/RNG'
import { START_LIVES, CELL } from '../../src/constants'
import { arg, parseSeeds } from '../lib/cli'
import { parseStageSpec, StageSpecError } from '../lib/stage-spec'

const difficulty = arg('difficulty') ?? 'hard'
const seeds = parseSeeds(arg('seeds'), 60)
const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(e instanceof StageSpecError ? e.message : `invalid --stages: ${stageSpec}`)
  process.exit(1)
}
const mode = Number(arg('mode', '3'))
/** §302 mode-7 along-mode split: 0 both / 1 wake / 2 level-ahead / 3 yield. */
const alongMode = Number(arg('along-mode', '0'))
const topN = Number(arg('top', '12'))
/** A merge burst shorter than this is a nudge, not a scene. */
const MIN_BURST = 24 // ticks (0.4 s)

/**
 * A GENUINE lane merge: the override fired, it CHANGED the chain's direction,
 * and the new direction is perpendicular to the target's travel axis (i.e. the
 * player is crossing lanes rather than merely continuing the chase).
 */
function isLateralMerge(t: Tick): boolean {
  if (!t.chg) return false
  return t.axis === 'V' ? t.mv === 'left' || t.mv === 'right' : t.mv === 'up' || t.mv === 'down'
}

interface Tick {
  /** Override fired (it may still agree with what the chain already picked). */
  over: boolean
  /** Override REPLACED the chain's direction — the only kind that is visible
   *  on screen. Measured: only ~45% of firings are of this kind. */
  chg: boolean
  /** AlongMode=3 yield tick: the override HELD (_moveDir = null) so a
   *  level/closing target could sweep past. Tracked via its own counter —
   *  `_moveDir` being null is shared with §182/§153, but `hold` is not. */
  hold: boolean
  /** The direction actually taken this tick (-null = hold). */
  mv: string
  col: number
  row: number
  dir: string
  fire: boolean
  /** Rising edge of `fire` — one per actual shot (fire stays true for
   *  several ticks; counting ticks over-counts by ~an order of magnitude). */
  shot: boolean
  onLane: boolean
  aligned: boolean
  kill: boolean
  laneGap: number
  /** Locked target id (tank.id) — used to reject windows where the AI keeps
   *  re-targeting (§170 目标摇摆); such windows are not reviewable as one
   *  coherent chase. */
  eId: number
  eCol: number
  eRow: number
  axis: string
  kills: number
}

interface Scene {
  stageIdx: number
  seed: number
  start: number
  end: number
  burstLen: number
  /** AlongMode=3 yield ticks inside the burst (0 for pure-merge scenes). */
  holdTicks: number
  alignedShots: number
  kills: number
  reachedLane: boolean
  laneShots: number
  /** Share of the window spent locked on the dominant target (0..1). */
  targetStable: number
  targetId: number
  path: string
  axis: string
  outcome: string
  totalTicks: number
}

function runOnce(
  stageIdx: number,
  seed: number,
): { ticks: Tick[]; outcome: string; kills: number } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const params = {
    ...DEFAULT_GOD_AI_PARAMS,
    pursuitTailMode: mode,
    pursuitTailAlongMode: alongMode,
  }
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], 0)
  input.reset()

  const ticks: Tick[] = []
  let lastOver = 0
  let lastChg = 0
  let lastHold = 0
  let lastKills = 0
  let prevFire = false
  let tick = 0
  while (tick < 36000) {
    sim.tick()
    const p = world.player
    const overNow = input._pursuitTailOverrides - lastOver > 0
    const chgNow = input._pursuitTailChanged - lastChg > 0
    const holdNow = input._pursuitTailHolds - lastHold > 0
    lastOver = input._pursuitTailOverrides
    lastChg = input._pursuitTailChanged
    lastHold = input._pursuitTailHolds
    const kill = world.killCount > lastKills
    lastKills = world.killCount

    let rec: Tick | null = null
    if (p && p.alive) {
      const pcCol = Math.round(p.x / CELL)
      const pcRow = Math.round(p.y / CELL)
      // The AI's ACTUAL locked target, not "nearest enemy". The nearest-enemy
      // proxy flips identity whenever two enemies swap distances, which makes
      // the lane/alignment columns meaningless (measured: in one 4-second
      // window the proxy jumped across five different tanks). _lastSelectTargetId
      // is written by every selectTarget() commit path.
      let best: (typeof world.tanks)[number] | null = null
      const lockedId = input._lastSelectTargetId
      if (lockedId >= 0) {
        for (const t of world.tanks) {
          if (t.id === lockedId && t.alive && t.spawnTimer <= 0) {
            best = t
            break
          }
        }
      }
      if (!best) {
        // Fall back to nearest (target = defense anchor / no enemy locked).
        let bestD = Infinity
        for (const t of world.tanks) {
          if (!t.alive || t.spawnTimer > 0) continue
          const d = Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
          if (d < bestD) {
            bestD = d
            best = t
          }
        }
      }
      if (best) {
        const eCol = Math.round(best.x / CELL)
        const eRow = Math.round(best.y / CELL)
        const vertical = Math.abs(best.vy) > Math.abs(best.vx)
        const onLane = vertical ? eCol === pcCol : eRow === pcRow
        const laneGap = vertical ? Math.abs(pcCol - eCol) : Math.abs(pcRow - eRow)
        const horizontalShot = p.dir === 'left' || p.dir === 'right'
        const aligned = horizontalShot ? eRow === pcRow : eCol === pcCol
        rec = {
          over: overNow,
          chg: chgNow,
          hold: holdNow,
          mv: input._moveDir ?? '-',
          col: pcCol,
          row: pcRow,
          dir: p.dir,
          fire: input._fire,
          shot: input._fire && !prevFire,
          onLane,
          aligned,
          kill,
          laneGap,
          eId: best.id,
          eCol,
          eRow,
          axis: vertical ? 'V' : 'H',
          kills: world.killCount,
        }
      }
    }
    prevFire = input._fire
    if (rec) ticks.push(rec)
    else
      ticks.push({
        over: overNow,
        chg: chgNow,
        hold: holdNow,
        mv: input._moveDir ?? '-',
        col: -1,
        row: -1,
        dir: '-',
        fire: false,
        shot: false,
        onLane: false,
        aligned: false,
        kill,
        laneGap: -1,
        eId: -1,
        eCol: -1,
        eRow: -1,
        axis: '-',
        kills: world.killCount,
      })
    input.endFrame()
    tick++
    if (world.state === 'stageclear' || world.state === 'gameover') break
  }
  return { ticks, outcome: world.state, kills: world.killCount }
}

/** Merge bursts: runs of override ticks with a small gap tolerance. */
function findScenes(stageIdx: number, seed: number, ticks: Tick[], outcome: string): Scene[] {
  const scenes: Scene[] = []
  let i = 0
  while (i < ticks.length) {
    if (!ticks[i].chg) {
      i++
      continue
    }
    // Walk the burst, tolerating brief interruptions (the AI may interleave a
    // dodge/reflex tick inside an otherwise continuous merge). AlongMode=3
    // yield ticks count as members too: a sustained hold only marks `chg` on
    // its first tick, so keying on `chg` alone would shatter one
    // wait-then-merge scene into a 1-tick stub plus a merge.
    let j = i
    let lastMemberTick = i
    let burstTicks = 0
    let lateralTicks = 0
    let holdTicks = 0
    while (j < ticks.length && j - lastMemberTick <= 12) {
      if (ticks[j].chg || ticks[j].hold) {
        burstTicks++
        if (isLateralMerge(ticks[j])) lateralTicks++
        if (ticks[j].hold) holdTicks++
        lastMemberTick = j
      }
      j++
    }
    const burstEnd = lastMemberTick
    // Require the burst to contain REAL lateral merges. The naive "override
    // fired" version was 55% no-ops (the merge direction happened to equal
    // directMove's pick) and produced review clips with no visible lane
    // change at all — caught by manual replay review, 2026-08-29.
    if (burstTicks < MIN_BURST || lateralTicks < 12) {
      i = burstEnd + 1
      continue
    }
    // Pure-merge bursts must be DOMINATED by real lateral merges. Hold-
    // containing bursts (AlongMode=3 yield-then-tail) are hold-dominant by
    // design — the wait is the point — so the share gate would reject
    // exactly the scenes this scan exists to find.
    if (holdTicks === 0 && lateralTicks < burstTicks * 0.5) {
      i = burstEnd + 1
      continue
    }
    // Extend: the payoff window is the aligned-fire / kill activity right
    // after the merge completes.
    let end = burstEnd
    let aligned = 0
    let laneShots = 0
    let kills = 0
    for (let k = i; k < Math.min(ticks.length, burstEnd + 240); k++) {
      if (ticks[k].shot && ticks[k].aligned) {
        aligned++
        if (ticks[k].onLane) laneShots++
        end = k
      }
      if (ticks[k].kill) {
        kills++
        end = k
      }
    }
    const reachedLane = ticks.slice(i, burstEnd + 1).some((t) => t.laneGap === 0)
    // Target stability: one tank must dominate the window, or the clip is a
    // re-targeting montage rather than a single chasable lane.
    const counts = new Map<number, number>()
    let span = 0
    for (let k = i; k <= Math.min(end, ticks.length - 1); k++) {
      if (ticks[k].eId < 0) continue
      span++
      counts.set(ticks[k].eId, (counts.get(ticks[k].eId) ?? 0) + 1)
    }
    let domId = -1
    let domN = 0
    for (const [id, n] of counts) {
      if (n > domN) {
        domN = n
        domId = id
      }
    }
    const stable = span > 0 ? domN / span : 0
    if (stable < 0.7) {
      i = burstEnd + 1
      continue
    }
    // Compact path signature for the human reviewer.
    const steps: string[] = []
    let last = ''
    for (let k = i; k <= Math.min(end, ticks.length - 1); k += 6) {
      const t = ticks[k]
      const tag = `${t.col},${t.row}`
      if (tag !== last) {
        steps.push(tag)
        last = tag
      }
    }
    scenes.push({
      stageIdx,
      seed,
      start: i,
      end,
      burstLen: lateralTicks,
      holdTicks,
      alignedShots: aligned,
      kills,
      reachedLane,
      laneShots,
      targetStable: stable,
      targetId: domId,
      path: steps.join('→'),
      axis: ticks[i].axis,
      outcome,
      totalTicks: ticks.length,
    })
    i = burstEnd + 1
  }
  return scenes
}

/**
 * 缺陷场景（baseline，mode 0）：玩家停在**并行车道**上横向开火。
 * 判据 = 窗口内 off-lane 且"最近敌在射击轴的交叉轴上"（perp shot）次数多、
 * 且目标正在沿自己的车道行进 —— 正是 §12.1 #3 描述的画面。
 */
function findDefectScenes(stageIdx: number, seed: number, ticks: Tick[], outcome: string): Scene[] {
  const scenes: Scene[] = []
  const W = 300 // 5 s window
  for (let i = 0; i + W < ticks.length; i += W) {
    let perp = 0
    let farOffLane = 0
    const steps: string[] = []
    let last = ''
    for (let k = i; k < i + W; k++) {
      const t = ticks[k]
      if (t.col < 0) continue
      // Sideways shot while sitting in a PARALLEL lane (gap ≥ 2), not adjacent.
      if (t.shot && !t.aligned && !t.onLane && t.laneGap >= 2) perp++
      if (!t.onLane && t.laneGap >= 2) farOffLane++
      const tag = `${t.col},${t.row}`
      if (tag !== last) {
        steps.push(tag)
        last = tag
      }
    }
    // Must actually be PURSUING (a parked tank plinking a wall is a different
    // bug), and must spend most of the window in the parallel lane.
    if (perp >= 3 && farOffLane > W * 0.45 && steps.length >= 5) {
      scenes.push({
        stageIdx,
        seed,
        start: i,
        end: i + W - 1,
        burstLen: 0,
        holdTicks: 0,
        alignedShots: 0,
        kills: ticks[i + W - 1].kills - ticks[i].kills,
        reachedLane: false,
        laneShots: perp,
        targetStable: 0,
        targetId: -1,
        path: steps.join('→'),
        axis: ticks[i].axis,
        outcome,
        totalTicks: ticks.length,
      })
    }
  }
  return scenes
}

/** 逐 0.25 s 解说一个窗口：玩家格 / 目标格 / 车道间隙 / 是否在车道 / 开火 / 击杀。 */
function narrate(ticks: Tick[], start: number, end: number): void {
  console.log('   tick   秒    玩家    目标   轴  车道间隙  在车道  移动  并道?  开火  对齐  击杀')
  for (let k = Math.max(0, start - 30); k <= Math.min(end + 30, ticks.length - 1); k += 15) {
    const t = ticks[k]
    const mark = k >= start && k <= end ? '>>' : '  '
    // A lane merge is a move PERPENDICULAR to the target's travel axis.
    const lateral =
      t.chg &&
      ((t.axis === 'V' && (t.mv === 'left' || t.mv === 'right')) ||
        (t.axis === 'H' && (t.mv === 'up' || t.mv === 'down')))
    console.log(
      `${mark}${String(k).padStart(6)} ${(k / 60).toFixed(1).padStart(5)}  ` +
        `${String(t.col).padStart(2)},${String(t.row).padStart(2)}  ` +
        `${String(t.eCol).padStart(2)},${String(t.eRow).padStart(2)}   ${t.axis}   ` +
        `${String(t.laneGap).padStart(4)}     ${t.onLane ? ' Y' : ' n'}    ` +
        `${t.mv.padEnd(5)}  ${
          t.hold ? ' 等待' : t.chg ? (lateral ? ' 并道' : ' 同向') : '   . '
        }   ` +
        `${t.fire ? 'F' : '.'}    ${t.aligned ? 'Y' : '.'}    ${t.kill ? '★' : '.'}`,
    )
  }
}

const narrateSpec = arg('narrate')
if (narrateSpec) {
  // --narrate <stageNo>:<seed>:<startTick>:<endTick>
  const [sNo, seed, start, end] = narrateSpec.split(':').map(Number)
  const { ticks, outcome } = runOnce(sNo - 1, seed)
  console.log(
    `§302 解说  s${sNo}(${STAGES[sNo - 1].name})@${seed} mode=${mode}  ` +
      `窗口 ${start}-${end} (${(start / 60).toFixed(1)}s-${(end / 60).toFixed(1)}s)  结局 ${outcome}`,
  )
  narrate(ticks, start, end)
  process.exit(0)
}

const defect = arg('defect') === '1' || arg('defect') === 'true'

const all: Scene[] = []
for (const si of stageIdxs) {
  for (const seed of seeds) {
    const { ticks, outcome } = runOnce(si, seed)
    all.push(
      ...(defect
        ? findDefectScenes(si, seed, ticks, outcome)
        : findScenes(si, seed, ticks, outcome)),
    )
  }
}

// Rank: for the defect scan, "laneShots" holds the perpendicular-shot count —
// rank purely on that (how many sideways shots the player wasted off-lane).
const score = (s: Scene) => (defect ? s.laneShots * 100 + s.kills * 40 : scoreMerge(s))
function scoreMerge(s: Scene) {
  return (
    Math.min(s.end - s.start, 420) +
    Math.min(s.end - s.start, 420) +
    60 * s.laneShots +
    25 * s.alignedShots +
    120 * s.kills +
    (s.reachedLane ? 150 : 0)
  )
}
all.sort((a, b) => score(b) - score(a))

const sec = (t: number) => (t / 60).toFixed(1)
console.log(
  `§302 ${defect ? '缺陷(并行车道横向开火)' : '并道射击'} 场景扫描  difficulty=${difficulty} ` +
    `mode=${mode}  stages=${stageIdxs.length} seeds=${seeds.length}  scenes=${all.length}  (tick → 秒 @60Hz)`,
)
console.log(
  defect
    ? 'rank  stage@seed        outcome   窗口 tick(秒)          横向空射 击杀 轴'
    : 'rank  stage@seed        outcome   窗口 tick(秒)          并道  等待  车道射击 对齐射击 击杀 入车道 轴',
)
all.slice(0, topN).forEach((s, i) => {
  const head =
    `${String(i + 1).padStart(4)}  s${String(s.stageIdx + 1).padStart(2)}(${STAGES[s.stageIdx].name})@${String(s.seed).padStart(2)}  ` +
    `${s.outcome.padEnd(10)} ` +
    `${String(s.start).padStart(5)}-${String(s.end).padStart(5)} (${sec(s.start)}s-${sec(s.end)}s, ${sec(s.end - s.start)}s)  `
  const tail = defect
    ? `${String(s.laneShots).padStart(6)}  ${String(s.kills).padStart(3)}   ${s.axis}`
    : `${String(s.burstLen).padStart(4)}  ${String(s.holdTicks).padStart(4)}  ${String(s.laneShots).padStart(6)}  ` +
      `${String(s.alignedShots).padStart(6)}  ` +
      `${String(s.kills).padStart(3)}  ${s.reachedLane ? '  Y' : '  n'}   ${s.axis}  锁定${(s.targetStable * 100).toFixed(0)}%`
  console.log(head + tail)
  console.log(`      玩家路径 ${s.path}`)
})
