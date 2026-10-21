import { describe, it, expect } from 'bun:test'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS } from '../src/ai/god/params.tables'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { evalBaseLaneSeal, BASE_LANE_SEAL } from '../src/ai/god/candidates/BaseLaneSeal'
import { ACTION_WEIGHTS } from '../src/ai/god/DecisionCore'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { BASE_POS } from '../src/constants'
import { placeEnemy, positionPlayer } from './helpers'

/**
 * A9 — 远程来袭弹的提前判线 + 封线 (DECISIONS §2026-10-21-godai-a9-base-lane-seal).
 *
 * 700 局审计：丢基地时玩家 p50 距离基地 9 格，基地 6 格内平均只有 0.23 个敌人，
 * 致命弹 down 40% / left 32% / right 28% —— 也就是**基地列上、隔着半张图的顺线
 * 点名**。baseLaneSentry(§198) 只覆盖玩家身边 range+1 的范围，远程射手没人管。
 *
 * 本候选：找到基地列上有活射界（canShootBase / canBreachRing）的远程射手 →
 * 走到该列「最靠近基地的可站格」→ 站位既挡弹、又获得向上的射击线 → 站定开火。
 */

function setup(mode: number) {
  const w = new World()
  w.rng.reseed(7)
  w.difficultyKey = 'hard'
  w.difficulty = DIFFICULTIES['hard']
  w.rules = RULES['hard'] ?? DEFAULT_RULES
  w.playerLevel = w.difficulty?.playerStartLevel ?? 0
  w.lives = w.difficulty?.startLives ?? 3
  w.loadStageData(STAGES[0], 0) // 真实关卡（有玩家 + 基地 + 环砖）
  // 清空基地列（rows 2..22）以获得受控的「远程顺线」场景，并拆掉环顶那格
  // (12,23) —— 只有环被拆穿，`canShootBase`（直射基地）才成立；环完好时敌人
  // 只能算「拆环威胁」，而 A9 默认只封直射（baseLaneSealCbr=0）。
  for (let r = 2; r <= 23; r++) {
    w.tileMap.set(BASE_POS.col, r, 'empty')
    w.tileMap.set(BASE_POS.col + 1, r, 'empty')
  }
  const ai = new GodAIInput(w, { ...DEFAULT_GOD_AI_PARAMS, baseLaneSealMode: mode })
  ai.reset()
  return { w, ai }
}

function ctxOf(w: World): DecisionContext {
  const p = w.player!
  return {
    w,
    p,
    pcx: p.x + p.w / 2,
    pcy: p.y + p.h / 2,
    onCooldown: false,
    aimDir: 'up',
    threat: null,
    shielded: false,
  } as DecisionContext
}

describe('A9 — baseLaneSeal', () => {
  it('ships ON (classic restore 0) and sits between closePickup(540) and midLaneDefense(545)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.baseLaneSealMode).toBe(1)
    // classic 是 bulletCap 1 的单发对枪，实测只坏不好 ⇒ 覆盖表里回退 0。
    expect(CLASSIC_MODEL_PARAMS.baseLaneSealMode).toBe(0)
    // 默认只封「直射基地」的射手。
    expect(DEFAULT_GOD_AI_PARAMS.baseLaneSealCbr).toBe(0)
    expect(ACTION_WEIGHTS.baseLaneSeal).toBe(542)
    expect(BASE_LANE_SEAL.id).toBe('baseLaneSeal')
  })

  it('mode 0 → never claims', () => {
    const { w, ai } = setup(0)
    placeEnemy(w, 12, 6)
    positionPlayer(w, 12, 20)
    expect(evalBaseLaneSeal(ai, ctxOf(w))).toBe(false)
  })

  it('seals the base column against a remote aligned shooter', () => {
    const { w, ai } = setup(1)
    placeEnemy(w, 12, 6) // on the base column, 18 cells from the base
    positionPlayer(w, 12, 20)
    const claimed = evalBaseLaneSeal(ai, ctxOf(w))
    expect(claimed).toBe(true)
    // Walks to the closest-to-base standable cell on that column (12,22).
    expect(ai._moveDir).not.toBe(null)
    expect(ai._lastBranch).toBe('baseLaneSeal')
  })

  it('holds the station and fires up the column when already there', () => {
    const { w, ai } = setup(1)
    placeEnemy(w, 12, 10) // within baseLaneSealRange(14) of the seal cell
    positionPlayer(w, 12, 22, 'up')
    expect(evalBaseLaneSeal(ai, ctxOf(w))).toBe(true)
    // Already on (12,22) facing up ⇒ hold position (no move) and shoot.
    expect(ai._moveDir).toBe(null)
    expect(ai._fire).toBe(true)
  })

  it('ignores shooters off the base column and shooters inside the near band', () => {
    {
      const { w, ai } = setup(1)
      placeEnemy(w, 8, 6) // same distance, wrong column
      positionPlayer(w, 12, 20)
      expect(evalBaseLaneSeal(ai, ctxOf(w))).toBe(false)
    }
    {
      const { w, ai } = setup(1)
      placeEnemy(w, 12, 21) // inside baseLaneSealMinDist (8)
      positionPlayer(w, 12, 18)
      expect(evalBaseLaneSeal(ai, ctxOf(w))).toBe(false)
    }
  })

  it('ignores a shooter with no live line to the base', () => {
    const { w, ai } = setup(1)
    placeEnemy(w, 12, 6)
    // Block the column between the shooter and the base with steel.
    for (let r = 10; r <= 14; r++) w.tileMap.set(12, r, 'steel')
    positionPlayer(w, 12, 20)
    expect(evalBaseLaneSeal(ai, ctxOf(w))).toBe(false)
  })
})
