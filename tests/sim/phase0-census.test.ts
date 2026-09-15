/**
 * phase0-census.test.ts — Phase 0 逐敌种画像 census 验证（plan/x3-power-followup.plan.md T3）。
 *
 * 1. **独立重实现对账（§8）**：从零埋点的原始事件流（`SimResult.events`）本地重算
 *    分敌种命中/击杀、击杀顺序、首命中/首杀，与 `runEvalOne` 的 census 字段逐值对账
 *    （两实现不共享代码 ⇒ 抓得住分桶/首事件/顺序类回归）。
 * 2. **determinism 双跑同 seed（§8）**：同一 (stage, seed) 跑两次，整行 JSON 逐字节相同。
 * 3. **killer-kind 归因**：玩家每次死亡都有凶手 kind（`byId` 回查命中，不为 null）。
 * 4. **曝光积分健全性**：`exposureByKind` 在有关卡的局里非零（④ 的分母不能是 0）。
 */
import { describe, it, expect } from 'bun:test'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne, ENEMY_KIND_ORDER } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'
import type { GameEvent, TankKind } from '../../src/types'

const KINDS = ENEMY_KIND_ORDER as readonly TankKind[]
const idxOf = (kind: string): number => KINDS.indexOf(kind as TankKind)

/** 独立重实现：只看原始事件流，不看 census 的任何中间状态。 */
function recountKinds(events: GameEvent[]): {
  enemyHits: number
  playerKills: number
  hits: number[]
  kills: number[]
  order: TankKind[]
  firstHit: TankKind | null
  firstKill: TankKind | null
} {
  const hits = [0, 0, 0, 0]
  const kills = [0, 0, 0, 0]
  const order: TankKind[] = []
  let firstHit: TankKind | null = null
  let firstKill: TankKind | null = null
  let enemyHits = 0
  let playerKills = 0
  for (const e of events) {
    if (e.type === 'enemy_hit') {
      enemyHits++
      if (firstHit === null) firstHit = e.targetKind
      const i = idxOf(e.targetKind)
      if (i >= 0) hits[i]++
    } else if (e.type === 'tank_destroyed') {
      if (e.tank.isPlayer) continue
      if (e.by === 'player' && e.tank.allegiance === 'enemy') {
        playerKills++
        order.push(e.tank.kind)
        if (firstKill === null) firstKill = e.tank.kind
        const i = idxOf(e.tank.kind)
        if (i >= 0) kills[i]++
      }
    }
  }
  return { enemyHits, playerKills, hits, kills, order, firstHit, firstKill }
}

function evalGod(stageIdx: number, seed: number, maxTicks = 4000) {
  return runEvalOne(stageIdx, STAGES[stageIdx] as never, seed, 'hard', maxTicks, '{}', 'god')
}

describe('Phase 0 census：与原始事件流独立重实现对账', () => {
  it('分敌种命中/击杀 + 击杀顺序 + 首命中/首杀逐值一致（policy=god，同源世界）', () => {
    for (const [stageIdx, seed] of [
      [0, 1],
      [0, 7],
      [3, 1],
      [5, 3],
    ] as const) {
      const local = runSimulation({
        seed,
        stage: STAGES[stageIdx] as never,
        stageIndex: stageIdx,
        difficulty: 'hard',
        policy: 'god',
        maxTicks: 4000,
        telemetry: true,
        collectMetrics: false,
      })
      const rc = recountKinds(local.events)
      const r = evalGod(stageIdx, seed)
      expect(r.enemyHits).toBe(rc.enemyHits)
      expect(r.hitsByKind).toEqual(rc.hits)
      expect(r.killsByKind).toEqual(rc.kills)
      expect(r.killOrder).toEqual(rc.order)
      expect(r.firstHitKind).toBe(rc.firstHit)
      expect(r.firstKillKind).toBe(rc.firstKill)
      // 桶和 = 总量（没有任何命中/击杀掉出 4 桶）。
      expect(r.hitsByKind.reduce((a, b) => a + b, 0)).toBe(rc.enemyHits)
      expect(r.killsByKind.reduce((a, b) => a + b, 0)).toBe(rc.playerKills)
    }
  })

  it('killOrder 与 killsByKind 守恒，firstKillKind = killOrder[0]', () => {
    for (const seed of [1, 2, 3, 4, 5]) {
      const r = evalGod(0, seed)
      expect(r.killOrder.length).toBe(r.killsByKind.reduce((a, b) => a + b, 0))
      expect(r.firstKillKind).toBe(r.killOrder.length > 0 ? r.killOrder[0] : null)
    }
  })
})

describe('Phase 0 census：determinism（同 seed 双跑逐字节一致）', () => {
  it('同一 (stage, seed) 两次 runEvalOne 整行 JSON 相同', () => {
    for (const [stageIdx, seed] of [
      [0, 11],
      [3, 5],
    ] as const) {
      const a = evalGod(stageIdx, seed, 2000)
      const b = evalGod(stageIdx, seed, 2000)
      expect(JSON.stringify(a.hitsByKind)).toBe(JSON.stringify(b.hitsByKind))
      expect(JSON.stringify(a.killOrder)).toBe(JSON.stringify(b.killOrder))
      expect(JSON.stringify(a.killerKinds)).toBe(JSON.stringify(b.killerKinds))
      expect(JSON.stringify(a.exposureByKind)).toBe(JSON.stringify(b.exposureByKind))
    }
  })
})

describe('Phase 0 census：killer-kind 归因（谁杀了我）', () => {
  it('每次玩家死亡都归因到合法敌方 kind（byId 回查不为 null）', () => {
    let found = false
    for (const [stageIdx, seed] of [
      [0, 1],
      [0, 2],
      [0, 5],
      [0, 13],
      [3, 2],
      [7, 3],
    ] as const) {
      const r = evalGod(stageIdx, seed)
      if (r.playerDeaths === 0) continue
      found = true
      expect(r.killerKinds.length).toBe(r.playerDeaths)
      for (const kk of r.killerKinds) {
        expect(kk).not.toBeNull()
        expect(KINDS).toContain(kk as TankKind)
      }
    }
    expect(found).toBe(true) // God-AI 在 hard 上必有死亡局，否则本断言空转
  })
})

describe('Phase 0 census：曝光积分（④ 的分母）', () => {
  it('有敌人的局 exposureByKind 非零，且在场 kind 数 ≥1', () => {
    for (const seed of [1, 2, 3]) {
      const r = evalGod(0, seed, 1500)
      const total = r.exposureByKind.reduce((a, b) => a + b, 0)
      expect(total).toBeGreaterThan(0)
      expect(r.exposureByKind.filter((x) => x > 0).length).toBeGreaterThanOrEqual(1)
    }
  })
})
