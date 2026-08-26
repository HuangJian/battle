import { describe, expect, it } from 'bun:test'
import {
  ACTIVATION_MATRIX,
  ALL_NON_REFLEX_LABELS,
  ANCHOR_HEAD_DIM,
  classifyCombatIntent,
  ENEMY_HEAD_DIM,
  enemySlotOrder,
  enemySlotOf,
  forwardMapLabel,
  INTENT_IDS,
  INTENT_DIM,
  isEndgameRegime,
  MIN_WINDOWS_PER_CLASS,
  REFLEX_TRANSPARENT_LABELS,
  segmentIntents,
  survivalMask,
  WHITELISTS,
  expandSegments,
  type TagFrame,
} from '../src/ai/intent/vocab'
import { CELL } from '../src/constants'
import { seedWorld, clearArena, placeEnemy } from './helpers'

/**
 * M0a 静态表达覆盖门（plan/Intent-Policy-NN-Plan.md §3.3 反向完备性 + §7 M0a gate）。
 *
 * 观测标签集 = 独立重述：从 src/ai/god/** 的 recordBranch/markBranch 调用点人工
 * 收敛（tests/stages.test.ts 的独立重实现惯例）。vocab 漏映射任何一个 ⇒ 失败。
 */
const OBSERVED_FINE_BRANCH_LABELS: readonly string[] = [
  // shell（think.ts 外壳态）
  'dead',
  'hold',
  // reflex 透明
  'dodge',
  'survive',
  // 候选提交标签
  't8',
  'baseLaneSentry',
  'defenseIntercept',
  'candidateIntercept',
  'candidateKill',
  'candidateReturn',
  'candidateClear',
  'powerup',
  'aggressive',
  't2a',
  'navigate',
  'firingLane',
  'carvePath',
  'baseConnectClear',
  'midLaneDefense',
  'midLaneHold',
  'suicideReturn',
]

describe('intent vocab — M0a 静态契约', () => {
  it('正向映射覆盖每一个观测细分支标签（无未挂靠标签）', () => {
    for (const label of OBSERVED_FINE_BRANCH_LABELS) {
      expect(() => forwardMapLabel(label)).not.toThrow()
    }
  })

  it('shell 与 reflex 标签按类别返回，不落入意图', () => {
    expect(forwardMapLabel('dead').kind).toBe('shell')
    expect(forwardMapLabel('hold').kind).toBe('shell')
    expect(forwardMapLabel('dodge').kind).toBe('reflex')
    expect(forwardMapLabel('survive').kind).toBe('reflex')
    expect(forwardMapLabel('t8')).toEqual({ kind: 'static', intent: 'INTERCEPT' })
    expect(forwardMapLabel('powerup')).toEqual({ kind: 'static', intent: 'PICKUP' })
    expect(forwardMapLabel('navigate').kind).toBe('combat-chain')
  })

  it('未知标签显式抛错（禁止静默兜底）', () => {
    expect(() => forwardMapLabel('no-such-label')).toThrow()
  })

  it('反向完备性断言 P0-1：每个非 reflex 细分支 ∈ ≥1 个白名单', () => {
    const whitelisted = new Set<string>()
    for (const rows of Object.values(WHITELISTS)) for (const r of rows) whitelisted.add(r.branch)
    for (const label of ALL_NON_REFLEX_LABELS) {
      expect(whitelisted.has(label)).toBe(true)
    }
  })

  it('白名单行引用的标签全部可解析且分层合法', () => {
    for (const [intent, rows] of Object.entries(WHITELISTS)) {
      expect(rows.length).toBeGreaterThan(0)
      for (const r of rows) {
        if (r.layer === 'reflex') {
          expect((REFLEX_TRANSPARENT_LABELS as readonly string[]).includes(r.branch)).toBe(true)
        } else {
          expect(OBSERVED_FINE_BRANCH_LABELS.includes(r.branch)).toBe(true)
        }
        if (r.suppressDodge) expect(r.layer).toBe('window')
      }
      void intent
    }
  })

  it('压制 dodge 标记唯一挂 suicideReturn（§116/§117 默认 OFF 反例规格）', () => {
    const marked: string[] = []
    for (const rows of Object.values(WHITELISTS))
      for (const r of rows) if (r.suppressDodge) marked.push(r.branch)
    expect(marked).toEqual(['suicideReturn'])
  })

  it('激活头矩阵 8×2 全定义且与计划草案一致', () => {
    expect(Object.keys(ACTIVATION_MATRIX).sort()).toEqual([...INTENT_IDS].sort())
    expect(ACTIVATION_MATRIX.INTERCEPT).toEqual({ enemy: 1, anchor: 0 })
    expect(ACTIVATION_MATRIX.HUNT).toEqual({ enemy: 1, anchor: 0 })
    expect(ACTIVATION_MATRIX.RETURN_DEFENSE).toEqual({ enemy: 0, anchor: 1 })
    expect(ACTIVATION_MATRIX.HOLD_LANE).toEqual({ enemy: 0, anchor: 1 })
    expect(ACTIVATION_MATRIX.CRUISE).toEqual({ enemy: 1, anchor: 1 })
    for (const c of ['CLEAR', 'PICKUP', 'ESCAPE'] as const) {
      expect(ACTIVATION_MATRIX[c]).toEqual({ enemy: 0, anchor: 0 })
    }
  })

  it('头维度常量：intent=8 / enemy=5 / anchor=16', () => {
    expect(INTENT_DIM).toBe(8)
    expect(ENEMY_HEAD_DIM).toBe(5)
    expect(ANCHOR_HEAD_DIM).toBe(16)
  })
})

describe('目标敌槽序函数（预注册 #25）', () => {
  it('按距基地 Manhattan 升序排槽；tie-break 行主扫描序', () => {
    const w = seedWorld(7)
    clearArena(w)
    // 距基地 (12,24)：等距对 (10,22)/(12,20) dist 均 4 → 行小者 (12,20) 先。
    const a = placeEnemy(w, 10, 22)
    const b = placeEnemy(w, 12, 20)
    // 近敌 (10,23) dist 3 应排第一；远敌 (2,4) dist 30 最后。
    const near = placeEnemy(w, 10, 23)
    const far = placeEnemy(w, 2, 4)
    const slots = enemySlotOrder(w)
    expect(slots[0].id).toBe(near.id) // d3
    expect(slots[1].id).toBe(b.id) // d4, row 20 < row 22
    expect(slots[2].id).toBe(a.id) // d4
    expect(slots[3].id).toBe(far.id) // d30
  })

  it('过滤非活体/玩家/spawning 敌；槽位查询双向一致', () => {
    const w = seedWorld(7)
    clearArena(w)
    const e1 = placeEnemy(w, 20, 20) // d12
    const e2 = placeEnemy(w, 6, 6) // d24
    const dead = placeEnemy(w, 1, 1)
    dead.alive = false
    expect(enemySlotOrder(w).map((t) => t.id)).toEqual([e1.id, e2.id])
    expect(enemySlotOf(w, e2.id)).toBe(2)
    expect(enemySlotOf(w, dead.id)).toBe(0)
    expect(enemySlotOf(w, 999999)).toBe(0)
  })

  it('确定性：同 World 双跑逐 id 一致；跨 World 格序一致（genId 为模块级单调计数）', () => {
    const buildCells = () => {
      const w = seedWorld(11)
      clearArena(w)
      placeEnemy(w, 3, 5)
      placeEnemy(w, 9, 9)
      placeEnemy(w, 15, 2)
      return enemySlotOrder(w).map((t) => ({
        col: Math.floor(t.x / CELL),
        row: Math.floor(t.y / CELL),
      }))
    }
    expect(buildCells()).toEqual(buildCells())

    const w = seedWorld(11)
    clearArena(w)
    placeEnemy(w, 3, 5)
    placeEnemy(w, 9, 9)
    placeEnemy(w, 15, 2)
    const once = enemySlotOrder(w).map((t) => t.id)
    expect(enemySlotOrder(w).map((t) => t.id)).toEqual(once)
  })

  it('px→cell 折算正确（tank px 坐标除以 CELL）', () => {
    const w = seedWorld(7)
    clearArena(w)
    const t = placeEnemy(w, 13, 25) // px (208, 400)
    expect(t.x).toBe(13 * CELL)
    expect(enemySlotOrder(w).length).toBe(1)
  })
})

describe('战斗链判定与 ENDGAME 谓词', () => {
  const base = {
    isBaseUnderThreat: false,
    playerDistToBase: 5,
    maxPlayerDistFromBase: 26,
    isEndgame: false,
  }

  it('预注册顺序：回防 > CRUISE > HUNT', () => {
    expect(classifyCombatIntent(base)).toBe('HUNT')
    expect(classifyCombatIntent({ ...base, isBaseUnderThreat: true })).toBe('HUNT') // 近基不成回防
    expect(classifyCombatIntent({ ...base, isBaseUnderThreat: true, playerDistToBase: 27 })).toBe(
      'RETURN_DEFENSE',
    )
    expect(classifyCombatIntent({ ...base, isEndgame: true })).toBe('CRUISE')
    // 回防压过 endgame：残局但基地告急仍须回家。
    expect(
      classifyCombatIntent({
        ...base,
        isEndgame: true,
        isBaseUnderThreat: true,
        playerDistToBase: 27,
      }),
    ).toBe('RETURN_DEFENSE')
    // 距离恰等于阈值（边界）不算回防距离外。
    expect(classifyCombatIntent({ ...base, isBaseUnderThreat: true, playerDistToBase: 26 })).toBe(
      'HUNT',
    )
  })

  it('isEndgameRegime 复用队列剩余口径', () => {
    const w = seedWorld(3)
    w.enemiesRemaining = 6
    expect(isEndgameRegime(w, 6)).toBe(true)
    expect(isEndgameRegime(w, 5)).toBe(false)
  })
})

describe('分段规则四件套（P0-2）', () => {
  const f = (label: string, combat: TagFrame['combat'] = null): TagFrame => ({ label, combat })
  const chainHunt = (): TagFrame['combat'] => ({
    isBaseUnderThreat: false,
    playerDistToBase: 5,
    maxPlayerDistFromBase: 26,
    isEndgame: false,
  })

  it('①去抖：<N tick 的孤立段并入前段', () => {
    // 10 tick navigate(HUNT) → 2 tick t8(INTERCEPT, blip) → 10 tick navigate。
    const frames: TagFrame[] = [
      ...Array.from({ length: 10 }, () => f('navigate', chainHunt())),
      ...Array.from({ length: 2 }, () => f('t8')),
      ...Array.from({ length: 10 }, () => f('navigate', chainHunt())),
    ]
    const segs = segmentIntents(frames)
    expect(segs).toEqual([{ start: 0, end: 21, intent: 'HUNT' }])
  })

  it('②reflex 透明：dodge 帧继承活跃意图，不打断分段', () => {
    const frames: TagFrame[] = [
      ...Array.from({ length: 8 }, () => f('t8')),
      f('dodge'),
      f('dodge'),
      ...Array.from({ length: 8 }, () => f('t8')),
    ]
    expect(segmentIntents(frames)).toEqual([{ start: 0, end: 17, intent: 'INTERCEPT' }])
  })

  it("shell 帧 'hold' 继承上一决策意图", () => {
    const frames: TagFrame[] = [
      ...Array.from({ length: 6 }, () => f('t8')),
      f('hold'),
      f('hold'),
      ...Array.from({ length: 6 }, () => f('powerup')),
    ]
    const segs = segmentIntents(frames)
    expect(segs).toEqual([
      { start: 0, end: 7, intent: 'INTERCEPT' },
      { start: 8, end: 13, intent: 'PICKUP' },
    ])
  })

  it('③局首短段归后段；整局透传帧产出空段', () => {
    const leadingBlip: TagFrame[] = [
      ...Array.from({ length: 2 }, () => f('t8')),
      ...Array.from({ length: 9 }, () => f('navigate', chainHunt())),
    ]
    expect(segmentIntents(leadingBlip)).toEqual([{ start: 0, end: 10, intent: 'HUNT' }])
    expect(segmentIntents([f('hold'), f('dodge'), f('dead')])).toEqual([])
  })

  it('稳定段正常切换：≥N 即成段', () => {
    const frames: TagFrame[] = [
      ...Array.from({ length: 6 }, () => f('t8')),
      ...Array.from({ length: 6 }, () => f('powerup')),
      ...Array.from({ length: 6 }, () => f('midLaneDefense')),
    ]
    expect(segmentIntents(frames)).toEqual([
      { start: 0, end: 5, intent: 'INTERCEPT' },
      { start: 6, end: 11, intent: 'PICKUP' },
      { start: 12, end: 17, intent: 'HOLD_LANE' },
    ])
  })

  it('战斗链帧经谓词在线判类（同分支不同状态不同意图）', () => {
    const huntChain = chainHunt()
    const returnChain: TagFrame['combat'] = {
      isBaseUnderThreat: true,
      playerDistToBase: 27,
      maxPlayerDistFromBase: 26,
      isEndgame: false,
    }
    const frames: TagFrame[] = [
      ...Array.from({ length: 6 }, () => f('navigate', huntChain)),
      ...Array.from({ length: 6 }, () => f('navigate', returnChain)),
    ]
    expect(segmentIntents(frames)).toEqual([
      { start: 0, end: 5, intent: 'HUNT' },
      { start: 6, end: 11, intent: 'RETURN_DEFENSE' },
    ])
  })

  it('expandSegments 段展开与转移边界帧定位', () => {
    const expanded = expandSegments(
      [
        { start: 0, end: 3, intent: 'HUNT' },
        { start: 4, end: 7, intent: 'CLEAR' },
      ],
      8,
    )
    expect(expanded).toEqual(['HUNT', 'HUNT', 'HUNT', 'HUNT', 'CLEAR', 'CLEAR', 'CLEAR', 'CLEAR'])
    const boundaries = expanded
      .map((v, i) => ({ v, i }))
      .slice(1)
      .filter((x, k) => x.v !== expanded[k])
    expect(boundaries.map((b) => b.i)).toEqual([4])
  })
})

describe('死类掩码约定（P0-3/#28）', () => {
  it('自然分布窗口 <200 的类降为 reflex-only 掩码', () => {
    const counts = {
      INTERCEPT: 5000,
      RETURN_DEFENSE: 199,
      HUNT: 12000,
      HOLD_LANE: 200,
      CLEAR: 300,
      PICKUP: 800,
      CRUISE: 4000,
      ESCAPE: 12,
    } as Record<(typeof INTENT_IDS)[number], number>
    const mask = survivalMask(counts)
    expect(mask.INTERCEPT).toBe(true)
    expect(mask.RETURN_DEFENSE).toBe(false) // 199 < 200
    expect(mask.HOLD_LANE).toBe(true) // 边界 200 恰好存活
    expect(mask.ESCAPE).toBe(false)
    expect(MIN_WINDOWS_PER_CLASS).toBe(200)
  })
})
