/**
 * shield-window.ts —— 「盾道具窗」粘性 tracker（aim-dodge-levers 批次，豁免机制 A）。
 *
 * 为什么存在：决定 A（`DECISIONS.md` §2026-10-01-goalnn-exempt-bakein）把新列的
 * 豁免**拌入**导出器：适用列只累计 `exposureExempt` 为假的拍（列与公式零变更）。
 * 谓词的盾侧**不是**「任意 `shieldTimer > 0`」——那是 v9 `threatLaneExempt` 的**宽**口径
 * （`src/nn/danger-metrics.ts`）；本批用用户 2026-10-01 拍板的**窄**口径：
 * **只有盾道具窗算豁免**，出生/复活盾（`RESPAWN_SHIELD_MS`）与星盾 grace
 * （`STAR_SHIELD_GRACE_MS`）**不计入**（plan/aim-dodge-levers.plan.md §1.2/§3.6）。
 *
 * 判定：逐 tick 观察 `player.shieldTimer` 的**增量**（不是静态阈值判断）：
 *   · 增量达到 `POWERUP_DURATION_MS` 量级 ⇒ 盾道具类（拾取恒为 `+= POWERUP_DURATION_MS`，
 *     叠加同；拾取发生在出生盾内（读数 3000+20000）也归此 ⇒ 优先级 盾道具 > 出生 > 星盾）；
 *   · 增量达到 `RESPAWN_SHIELD_MS` 量级 ⇒ 出生/复活类；
 *   · 其余正增量 ⇒ 星盾类（hard 星盾臂恒 0，仅哨兵）。
 * 类**粘性**：盾道具窗从 20000 衰减到 ≤ 3000 时读数像出生盾，但类保持「盾道具」
 * 直到 timer 归零 —— 这就是粘性存在的原因（探针 v4/v5 已验证该形态）。
 *
 * 阈值**全部从 `src/constants.ts` 派生**（禁裸写数字）：调参改常量 ⇒ tracker
 * 自动跟随；`tests/sim/exposure-levers.test.ts` 有哨兵钉住（阈值 === 常量）。
 *
 * ⚠ 同类实现**不得**在导出器里 inline：训练（`export-rl-rollout`）与评估
 * （`export-eval-game`）两侧共用本文件（plan §3.5）。
 *
 * 纯状态机、不读 rng、不写 World。它持有的分类不是玩法状态（AGENTS §2.2 只管
 * `World` 内的玩法状态），是分析侧跨 tick 的派生量 —— 与 v9 `geoFallback` 计数器同性质。
 */
import { POWERUP_DURATION_MS, RESPAWN_SHIELD_MS, STAR_SHIELD_GRACE_MS } from '../constants'
import type { World } from '../game/World'

/** 无盾（timer 归零后清类）。 */
export const SHIELD_CLASS_NONE = 0
/** 出生/复活盾（`RESPAWN_SHIELD_MS`）——**不算**豁免（窄口径）。 */
export const SHIELD_CLASS_SPAWN = 1
/** 盾道具窗（`POWERUP_DURATION_MS`）——**算**豁免（A 谓词的盾侧）。 */
export const SHIELD_CLASS_POWERUP = 2
/** 星盾 grace（`STAR_SHIELD_GRACE_MS`）——**不算**豁免（hard 臂恒 0）。 */
export const SHIELD_CLASS_STARLIKE = 3

/**
 * 粘性盾窗分类器。`update(world)` 每 tick 调用恰一次（沿 tick 升序），
 * `powerupActive()` 供 `exposureExempt` 取盾侧谓词。
 */
export interface ShieldWindow {
  /** 喂一拍世界状态（每 tick 一次；导出器在 consumeEvents 之后调）。 */
  update(world: World): void
  /** 当前是否处于「盾道具窗」——豁免机制 A 的盾侧谓词。 */
  powerupActive(): boolean
  /** 当前分类（`SHIELD_CLASS_*`；测试/审计用）。 */
  classOf(): number
  /** 新局清空（跨局复用卫生；`ShotRec`/patches 同规，plan §6.1-6）。 */
  reset(): void
}

/**
 * 建一个 tracker（**每局一个**；闭包持有 `prev` 与分类，不落任何全局）。
 * 浮点余量 0.5ms 只防同值抖动；真实增量是 20000 / 3000 / 1500 量级。
 */
export function createShieldWindow(): ShieldWindow {
  let cls: number = SHIELD_CLASS_NONE
  let prev = 0
  return {
    update(world: World): void {
      const st = world.player?.shieldTimer ?? 0
      if (st - prev > 0.5) {
        // 按**增量后的绝对值**分类（对「衰减后再设盾」的边角也稳；探针 v4 已验证形态）。
        if (st >= POWERUP_DURATION_MS) cls = SHIELD_CLASS_POWERUP
        else if (st >= RESPAWN_SHIELD_MS) cls = SHIELD_CLASS_SPAWN
        else if (st >= STAR_SHIELD_GRACE_MS) cls = SHIELD_CLASS_STARLIKE
        else cls = SHIELD_CLASS_NONE // 未知的小增量：不视作任何已识别窗
      }
      if (st <= 0) cls = SHIELD_CLASS_NONE
      prev = st
    },
    powerupActive(): boolean {
      return cls === SHIELD_CLASS_POWERUP
    },
    classOf(): number {
      return cls
    },
    reset(): void {
      cls = SHIELD_CLASS_NONE
      prev = 0
    },
  }
}
