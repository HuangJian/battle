import type { Bullet } from '../../src/types'

/**
 * aim-dodge-levers shot registry（plan/aim-dodge-levers.plan.md §3.1/§3.2/§3.4/§3.5/§7.1）。
 *
 * 训练侧（`export-rl-rollout.ts`）与评估侧（`export-eval-game.ts`）**共用同一实现** ——
 * 「登记后至多结算一次」（settle-once，逐弹去重）与所有权校验只在这里写一遍；
 * 两端的差别只在结算后怎么记账（训练侧补回写区间 + 加 tel；评估侧直接计数）。
 * 双端各写一套 registry 曾是本批最可能的静默分叉点（§3.5 双端一致性）。
 *
 * 关键不变量（sb P0-B 真值表在 `tests/sim/aim-shot-registry.test.ts` 钉死）：
 *   · **结算即出表**：同一 id 第二次 `takeAimShot` 必得 `null`（一弹拆 2×2 四格、
 *     双亡对消双边等重复事件只落一次）；
 *   · **所有权匹配**：玩家结算路径拿不到敌弹记录，反之亦然（`bullet_cancelled` 的
 *     双边查表天然只命中玩家那一边）；
 *   · **未登记 = 不入桶**：「交棒弹」（state-init 时已在飞、本局没有 `bullet_fired`
 *     事件）从不入表 ⇒ 它命中也拿不到 rec ⇒ 不进任何桶、恒等式仍严格（§3.1）。
 *
 * 纯数据层：只读写 `Map`，不读 World / RNG，不进 `tickHash`。
 */
export interface AimShotRec {
  /** 弹 id（Map 键同步存；结算删表用）。 */
  id: number
  /** true = 玩家弹（开火结果族）；false = 敌弹（承伤族）。 */
  isPlayer: boolean
  /** 开火时刻**玩家中心格**（仅玩家弹有意义；敌弹为 0）。 */
  fireCol: number
  fireRow: number
  /** 回写起点：开火时 `shard.metrics.length`（训练侧用；评估侧无行序 ⇒ 0）。 */
  patchFrom: number
  /** 敌弹逐 tick 火线权重累计（A 拌入：冻/盾道具窗拍不新增）。 */
  laneWeight: number
  /** 弹对象引用（局末兜底/调试；不进 gameplay）。 */
  bulletRef: Bullet
}

/** `bulletId → AimShotRec` 登记表。 */
export type AimShotTable = Map<number, AimShotRec>

/** 登记一颗玩家弹（`bullet_fired` 的玩家分支；开火结果族）。 */
export function registerPlayerAimShot(
  table: AimShotTable,
  id: number,
  fireCol: number,
  fireRow: number,
  patchFrom: number,
  bulletRef: Bullet,
): void {
  table.set(id, { id, isPlayer: true, fireCol, fireRow, patchFrom, laneWeight: 0, bulletRef })
}

/** 登记一颗敌弹（`bullet_fired` 的敌方分支；承伤族）。 */
export function registerEnemyAimShot(
  table: AimShotTable,
  id: number,
  patchFrom: number,
  bulletRef: Bullet,
): void {
  table.set(id, {
    id,
    isPlayer: false,
    fireCol: 0,
    fireRow: 0,
    patchFrom,
    laneWeight: 0,
    bulletRef,
  })
}

/**
 * 结算一颗登记弹：命中 / 真拆砖 / 对消三条口径共用（settle-once 本体）。
 *
 * 返回 `null` = 查不到（交棒弹 / 从未开火）、已结算、或所有权不匹配 ⇒ 调用方一律
 * 当 no-op（不入桶、不补回写、不计数）。
 */
export function takeAimShot(table: AimShotTable, id: number, isPlayer: boolean): AimShotRec | null {
  const rec = table.get(id)
  if (!rec || rec.isPlayer !== isPlayer) return null
  table.delete(rec.id)
  return rec
}
