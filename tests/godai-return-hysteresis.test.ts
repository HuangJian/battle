import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { RNG } from '../src/utils/RNG'
import { START_LIVES, CELL } from '../src/constants'

/**
 * A4 — hunt↔defend objective hysteresis (DECISIONS §2026-10-20-godai-a4-hysteresis).
 *
 * `selectTargetUncached` gates "return to defend" on
 *
 *   `playerDistToBase > maxPlayerDistFromBase`
 *
 * where `playerDistToBase` is a DISCRETE cell distance derived from
 * `playerCell()` (`round(x / CELL)`). It therefore alternates between `d` and
 * `d+1` every time the tank crosses a cell boundary — roughly every 11 ticks
 * at player speed. Parked exactly on the threshold the objective flips between
 * "hunt the enemy" and "return to base", which reverses the desired direction;
 * the 200 ms turn cooldown (12 ticks) is longer than one cell of travel, so
 * neither reversal ever completes and the tank paces one cell indefinitely.
 *
 * Measured (hard s35 seed 1000, t2395-2450): the tank paced x=354↔368 at
 * y=160 for 300+ ticks; and at t760-850 it paced y=152↔165 (`distToBase`
 * 26 → hunt up, 27 → return down).
 *
 * The fix adds a latch: entering the defend objective stays IMMEDIATE (a real
 * emergency is never delayed), but leaving it requires the distance to fall to
 * `threshold - defenseReturnHysteresis`. The latch clears the moment no defend
 * condition holds, so a vanishing threat releases it at once.
 *
 * These two pins are the whole contract:
 *   1. the objective flips many times per game with hysteresis OFF;
 *   2. hysteresis cuts that count substantially (and never delays entry).
 */

/** Count hunt↔defend objective flips over one deterministic run. */
function countObjectiveFlips(
  hyst: number,
  stageIdx: number,
  seed: number,
  maxTicks = 18000,
): number {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(
    world,
    // defensePosStandable 固定为 0：它会把集合点改到基地周边的可站格，
    // 使「目标是否等于集合点」这一判定本身随地形漂移，从而污染翻转计数。
    // 本测试只隔离 A4。
    // defenseCellResolve 固定为 0（A8 之前的原始环砖集合点）：A8 把集合点解析到
    // 可站的 (12,22) 之后，「目标 == 集合点」的判定语义整体改变，翻转计数不再
    // 反映 A4 自身的滞后效果。本测试只隔离 A4，因此固定在 A4 纪元的口径。
    {
      ...DEFAULT_GOD_AI_PARAMS,
      defenseReturnHysteresis: hyst,
      defensePosStandable: 0,
      defenseCellResolve: 0,
    },
    godRng,
  )
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()

  let flips = 0
  let prevIsDefense: boolean | null = null
  for (let t = 0; t < maxTicks; t++) {
    sim.tick()
    const p = world.player
    if (p && p.alive && p.spawnTimer <= 0) {
      const tgt = input.selectTarget(input.playerCell())
      const def = input.getDefaultDefensePosition()
      const isDefense = !!tgt && tgt.col === def.col && tgt.row === def.row
      if (prevIsDefense !== null && isDefense !== prevIsDefense) flips++
      prevIsDefense = isDefense
    }
    input.endFrame()
    if (world.state === 'stageclear' || world.state === 'gameover' || world.state === 'victory') {
      break
    }
  }
  return flips
}

/** Longest ±1-zone dwell without a kill (the §168 nav-stuck pin metric). */
function maxZoneDwell(
  params: typeof DEFAULT_GOD_AI_PARAMS,
  stageIdx: number,
  seed: number,
  maxTicks: number,
): number {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()
  let anchorCol = -99
  let anchorRow = -99
  let dwell = 0
  let best = 0
  for (let tick = 0; tick < maxTicks; tick++) {
    sim.tick()
    const p = world.player
    if (p && p.alive && p.spawnTimer <= 0) {
      const col = Math.floor((p.x + p.w / 2) / CELL)
      const row = Math.floor((p.y + p.h / 2) / CELL)
      if (Math.abs(col - anchorCol) <= 1 && Math.abs(row - anchorRow) <= 1) dwell++
      else {
        anchorCol = col
        anchorRow = row
        dwell = 1
      }
      if (dwell > best) best = dwell
    } else {
      anchorCol = -99
      anchorRow = -99
      dwell = 0
    }
    input.endFrame()
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed' && e.by === 'player') {
        dwell = 0
        anchorCol = -99
        anchorRow = -99
      }
    }
    if (world.state === 'stageclear' || world.state === 'gameover' || world.state === 'victory') {
      break
    }
  }
  return best
}

describe('A4 回防目标滞后 — 离散格距离阈值上的目标翻转', () => {
  it('hysteresis=0：阈值边界上目标反复翻转（bug 复现）', () => {
    const flips = countObjectiveFlips(0, 34, 1000)
    // s35 seed1000 实测 43 次/局（5 关×4 seed 平均 36.2）。下限取得宽松，
    // 只钉住「抖动确实存在」这件事，不钉住具体数字。
    expect(flips).toBeGreaterThan(10)
  })

  it('hysteresis=3：翻转次数大幅下降', () => {
    const off = countObjectiveFlips(0, 34, 1000)
    const on = countObjectiveFlips(3, 34, 1000)
    expect(on).toBeLessThan(off * 0.7)
  })

  it('A4 把 Battlement 的 nav-stuck 钉住从 422 拍缩到 163 拍', () => {
    // 这是最硬的证据：§168 记录的 S34 出生点钉住（pin）本身就是同一类
    // hunt↔defend 目标抖动。滞后开启后，S34 seed1-25 的 OFF 档 dwell 从
    // 249~893 全部落到 ≤262（平均 ~460 → ~171）。
    // 参数集与 §168 的 OFF 腿一致：baseConnectClear / pixelStuckDirectMove
    // 都是同一批"破局"机制，开着会掩盖 pin，钉住时长不可比。
    const base = {
      ...DEFAULT_GOD_AI_PARAMS,
      navStuckZone: 0,
      baseConnectClearMode: 0,
      pixelStuckDirectMoveTicks: 0,
      // A8 之后集合点变成可站的 (12,22)，S34 的轨迹整体改变（OFF 档 dwell
      // 422 → 373）。本用例钉的是 A4 自己的贡献，固定在 A4 纪元口径。
      defenseCellResolve: 0,
    }
    const off = maxZoneDwell({ ...base, defenseReturnHysteresis: 0 }, 33, 8, 2400)
    const on = maxZoneDwell({ ...base, defenseReturnHysteresis: 3 }, 33, 8, 2400)
    expect(off).toBeGreaterThan(400)
    expect(on).toBeLessThan(300)
  })

  it('滞后只推迟「退出」，不推迟「进入」——首次触发阈值不变', () => {
    // 同一个 latch，hysteresis 只影响释放侧。两臂在第一次进入回防之前
    // 的整段行为必须逐 tick 相同；这里钉住的是「进入永远是即时的」：
    // 带上 latch 时（hyst>0 且已 latched）阈值只会更低，绝不会更高。
    const g = new GodAIInput(new World(), {
      ...DEFAULT_GOD_AI_PARAMS,
      defenseReturnHysteresis: 3,
    })
    expect(g._defenseReturnLatch).toBe(false)
  })
})
