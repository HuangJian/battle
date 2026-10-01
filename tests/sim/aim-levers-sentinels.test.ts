/**
 * aim-levers-sentinels.test.ts — 口径源码哨兵（plan/aim-dodge-levers.plan.md §7.1 末条）。
 *
 * 「源码哨兵」是本仓库对**无法用行为用例稳定构造**的接线事实的惯用手法
 * （先例：tests/sim/metrics-v10-exposure.test.ts 的「导出器接线（源码哨兵）」）。
 * 这里钉住四类：
 *   ① 事件扩展：`bulletId`/`bullet_cancelled` 的 push 点与数量（加一处/漏一处都红）；
 *   ② 事件序：metrics 行在 `sim.tick()` **之前**推、事件在 tick **之后**消费，
 *      回写只在**打包前一次**应用（挪动 = 信用静默挪位，§7.1）；
 *   ③ 口径作废项：无 `BULLET_CANCEL_DIST_PX`（对消走真事件，sb P1-F）；
 *   ④ raw-only 名单（§3.6）与 A/宽口径差异有头注（`exposureExempt` ≠ `threatLaneExempt`）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = join(import.meta.dir, '..', '..')
const read = (p: string): string => readFileSync(join(root, p), 'utf8')

describe('口径源码哨兵：事件扩展（P2）', () => {
  const combat = read('src/game/SimulationCombat.ts')

  it('spendStarShield(tank, bullet)：星盾消耗也有弹归因（sb P0-A）', () => {
    expect(combat).toContain('private spendStarShield(tank: Tank, bullet: Bullet)')
  })

  it('player_damage / player_hit（致死 + 星盾两条）都带 bulletId', () => {
    expect(combat).toContain("type: 'player_damage', damage: bullet.damage, bulletId: bullet.id")
    // 两条 player_hit push：致死路径 + spendStarShield 路径
    const hits = combat.match(/type: 'player_hit', bulletId: bullet\.id/g) ?? []
    expect(hits.length).toBe(2)
  })

  it('bullet_cancelled 唯一 push 点 = bulletHitsBullet（对消真事件）', () => {
    const pushes = combat.match(/type: 'bullet_cancelled'/g) ?? []
    expect(pushes.length).toBe(1)
    expect(combat).toContain(
      "w.pushEvent({ type: 'bullet_cancelled', aId: bullet.id, bId: other.id })",
    )
  })

  it('terrain_destroyed 四处都带 bulletId（brick 桶归因口）', () => {
    const pushes = combat.match(/type: 'terrain_destroyed',/g) ?? []
    expect(pushes.length).toBe(4)
    const ids = combat.match(/bulletId: bullet\.id/g) ?? []
    expect(ids.length).toBeGreaterThanOrEqual(4)
  })
})

describe('口径源码哨兵：训练导出器（P3）', () => {
  const roll = read('tools/sim/export-rl-rollout.ts')

  it('事件序：metrics 行在 sim.tick() 之前推，事件在 tick 之后消费', () => {
    const push = roll.indexOf('shard.metrics.push(metricsRow())')
    const tick = roll.indexOf('\n    sim.tick()')
    const consume = roll.indexOf('for (const e of world.consumeEvents())')
    expect(push).toBeGreaterThan(-1)
    expect(tick).toBeGreaterThan(push) // 先推行（本决策步快照），再跑 tick
    expect(consume).toBeGreaterThan(tick) // 结算在本 tick 事件消费里
  })

  it('回写只在打包前应用一次（applySuffixPatches），不逐 tick 应用', () => {
    const defs = roll.match(/applySuffixPatches\(/g) ?? []
    expect(defs.length).toBe(2) // 1 定义 + 1 调用
    const apply = roll.indexOf('applySuffixPatches(shard.metrics, patches)')
    const score = roll.indexOf('const scored = scoreRun(')
    expect(apply).toBeGreaterThan(-1)
    expect(apply).toBeLessThan(score) // 打包/打分前一次应用
  })

  it('METRICS_VERSION = 10（v10 全批未签入；本批并入不升版，sb P0-E）', () => {
    expect(roll).toContain('export const METRICS_VERSION = 10')
  })

  it('对消几何近似作废：无 BULLET_CANCEL_DIST_PX', () => {
    for (const f of [
      'tools/sim/export-rl-rollout.ts',
      'tools/sim/export-eval-game.ts',
      'src/nn/danger-metrics.ts',
    ]) {
      expect(read(f)).not.toContain('BULLET_CANCEL_DIST_PX')
    }
  })

  it('raw-only 名单有「仅审计，不得定价」注记（§3.6）', () => {
    expect(roll).toContain('仅审计，不得定价')
    expect(read('nn-training/rl/reward_library.py')).toContain('仅审计不得定价')
  })
})

describe('口径源码哨兵：豁免 A vs 宽口径（§3.6）', () => {
  const dm = read('src/nn/danger-metrics.ts')
  const sw = read('src/nn/shield-window.ts')

  it('danger-metrics 头注写明 A 谓词与 threatLaneExempt 不是同一件事', () => {
    expect(dm).toContain('exposureExempt')
    expect(dm).toContain('threatLaneExempt')
    expect(dm).toContain('不是同一件事')
  })

  it('shield-window 头注写明盾侧不是「任意 shieldTimer > 0」', () => {
    expect(sw).toContain('threatLaneExempt')
    expect(sw).toContain('宽')
  })

  it('v10 批次整理：Python 侧不再有 enclExempt 三列（决定 A 取代）', () => {
    const py = read('nn-training/rl/reward_library.py')
    expect(py).not.toContain('enclExempt1Ticks')
    expect(py).not.toContain('enclExempt2Ticks')
    expect(py).not.toContain('enclExempt3pTicks')
  })
})
