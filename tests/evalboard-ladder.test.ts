/** evalboard-ladder.test.ts ↔ tools/training/evalboard/ladder.ts（§10.2 单测清单）。 */
import { describe, expect, it } from 'bun:test'
import {
  buildLadder,
  costGate,
  evaluateGate,
  mapHashOf,
  markProvisional,
  nextPos,
  stageJsonOf,
  stageS1NoBase,
  type LadderRung,
} from '../tools/training/evalboard/ladder'
import { decodeStageGrid } from '../src/nn/config-stage'
import { deriveMetrics } from '../tools/training/evalboard/stats'
import type { EvalGameRow } from '../tools/training/evalboard/store'

function metrics(over: Partial<ReturnType<typeof deriveMetrics>> = {}) {
  return {
    n: 400,
    winRate: 0.5,
    clearRate: 0.5,
    killCompletion: 0.9,
    lifePrice: 2,
    winDmgMedian: 1,
    winTickMedian: 1000,
    deathRate: 0.1,
    timeoutRate: 0.05,
    accuracy: 0.2,
    shotsPerGame: 50,
    firstKillTickMedian: 200,
    stuckP95: 5,
    cellsVisitedMean: 30,
    ...over,
  }
}

function rung(over: Partial<LadderRung> = {}): LadderRung {
  return { ...buildLadder()[0], ...over }
}

describe('阶梯定义 (§4.1/§4.2)', () => {
  it('8 rung，维度顺序敌数→命数→地形→基地', () => {
    const l = buildLadder()
    expect(l.map((r) => r.id)).toEqual([
      'c4l1',
      'c6l1',
      'c8l2',
      'c10l2',
      'c14l3',
      'c20l3',
      's1l3b0',
      's1l3b1',
    ])
    expect(l[0].lives).toBe(1)
    expect(l[6].difficulty).toBe('hard')
    // s1l3b0 去基地：无 E 子块；s1l3b1 有
    expect(stageS1NoBase().tiles.join('')).not.toContain('E')
  })
  it('mapHash 改 grid 即变（改 grid 不改 id 不复用旧局）', () => {
    const l = buildLadder()
    const alt = { ...l[0].stage, tiles: [...l[0].stage.tiles] }
    alt.tiles[0] = `#${alt.tiles[0].slice(1)}`
    expect(mapHashOf(alt)).not.toBe(l[0].mapHash)
  })
  it('God<30% 自动标 provisional', () => {
    const l = markProvisional(
      buildLadder().map((r, i) =>
        i === 2 ? { ...r, god: { winRate: 0.24, lifePrice: 3, n: 1600, provisional: null } } : r,
      ),
    )
    expect(l[2].god.provisional).toBe(true)
    expect(l[0].god.provisional).toBeNull()
  })
})

describe('过门 (§4.4)', () => {
  it('过门进格 / 未过停', () => {
    const god = metrics({ winRate: 0.64, lifePrice: 1.5 })
    const pass = evaluateGate({
      student: metrics({ winRate: 0.5, lifePrice: 2 }),
      god,
      godWinRate1600: 0.64,
    })
    expect(pass.main).toBe(true) // 0.5 ≥ 0.7×0.64=0.448
    expect(pass.pass).toBe(true)
    expect(nextPos(0, rung(), pass)).toEqual({ action: 'pass', next: 1 })
    const fail = evaluateGate({
      student: metrics({ winRate: 0.3, lifePrice: 2 }),
      god,
      godWinRate1600: 0.64,
    })
    expect(fail.main).toBe(false)
    expect(nextPos(0, rung(), fail)).toEqual({ action: 'stay' })
  })
  it('partial 不判；熔断停', () => {
    expect(nextPos(0, rung(), null, { partial: true })).toEqual({ action: 'partial' })
    expect(nextPos(0, rung(), null)).toEqual({ action: 'partial' })
    expect(nextPos(0, rung(), null, { fused: 'S2' })).toEqual({ action: 'fuse-stop', reason: 'S2' })
  })
  it('provisional 穿过', () => {
    const r = rung({ god: { winRate: 0.2, lifePrice: 3, n: 1600, provisional: true } })
    expect(nextPos(2, r, null)).toEqual({ action: 'provisional-pass', next: 3 })
  })
  it('wins=0 ⇒ lifePrice=+∞ ⇒ 代价门自动不过', () => {
    const inf = deriveMetrics([{ win: false, playerHits: 9 } as unknown as EvalGameRow]).lifePrice
    expect(inf).toBe(Infinity)
    expect(costGate(Infinity, 1.5)).toBe(false)
  })
  it('代价门双边 clamp：God 命价高不放水，命价低不反超老师', () => {
    expect(costGate(13.5, 4.5)).toBe(true) // 3×4.5=13.5 上界
    expect(costGate(13.6, 4.5)).toBe(false)
    expect(costGate(0.4, 0.5)).toBe(true) // 3×0.5=1.5，0.4≤1.5 过
    expect(costGate(1.6, 0.5)).toBe(false) // 0.6→1.6 越过 1.5 门不过
  })
})

describe('rung 载荷可执行 (§4.2)', () => {
  it('stageJsonOf → decodeStageGrid 往返：tiles/enemyCount/spawns 一致', () => {
    for (const r of buildLadder()) {
      const st = decodeStageGrid(stageJsonOf(r), 2000 + r.idx, 860001)
      expect(st.tiles).toEqual(r.stage.tiles)
      expect(st.enemyCount).toBe(r.stage.enemyCount ?? r.stage.enemies.length)
      expect(st.enemies.slice(0, st.enemyCount)).toEqual(r.stage.enemies.slice(0, st.enemyCount))
      if (r.stage.playerSpawn) expect(st.playerSpawn).toEqual(r.stage.playerSpawn)
      if (r.stage.enemySpawns) expect(st.enemySpawns).toEqual(r.stage.enemySpawns)
    }
  })
  it('tiles26 非法载荷响亮报错', () => {
    expect(() => decodeStageGrid('{"grid":[],"tiles26":[".."]}', 2000)).toThrow()
  })
})
