/**
 * intent-player.ts — M4-C IntentPlayer：意图策略网 + 最小执行器 stub
 * (plan/Intent-Policy-NN-Plan.md §4 M4；I6：runSimulation({policy:'intent'}) 注册)。
 *
 * 结构：
 *   - 每 replan 窗口（INTENT_REPLAN_TICKS=30）编码 obs → intentForward(obs,scalars,
 *     inject) → argmax 意图（死类掩码：ESCAPE 依 <200 窗口反射掩码剔除）；
 *   - 注入特征 prev-intent(one-hot 8)+duration 由本类维护（与 tagger 相同语义，纯
 *     观察、零 RNG）；
 *   - 最小执行器 stub（M4 界定：非 M6 真执行器——共享委托/三层契约在 M6 交付）。
 *     3 意图原语全部直读 World 数据，无副本无 GodAIInput：
 *       HUNT             → 向最近敌移动、对齐即开火
 *       INTERCEPT        → 基地威胁敌优先（威胁半径内），否则回防基地方向
 *       RETURN_DEFENSE   → 基地方向
 *     默认兜底 = HUNT（CRUISE/PICKUP/CLEAR/HOLD_LANE/ESCAPE 在 M4 stub 一律并入
 *     HUNT，行为语义由 M6 执行器完善——这是本里程碑允许的最小闭环，避免复制第二
 *     份实现）。
 *
 * 确定性：意图网络为纯前向（固定权重）；无 world.rng 消费；同 seed 双跑逐字节一致
 * （M4 stub 单测断言）。
 */
import { type Direction } from '../constants'
import { BASE_POS, MAX_ENEMIES_ALIVE } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { Tank } from '../types'
import { ObsEncoder } from './obs-encoder'
import { buildIntentModelFromText, type IntentModelLike } from './infer'
import { INTENT_IDS } from '../ai/intent/vocab'
import { INTENT_REPLAN_TICKS } from '../ai/intent/tagger'

/** 死类掩码：reflex-only 类（ESCAPE）不参与 argmax——logit 置 -Inf。 */
const MASKED_INTENTS: ReadonlySet<string> = new Set(['ESCAPE'])

function argmaxLogits(logits: Float32Array, masked: ReadonlySet<string>): number {
  let best = 0
  let bestV = masked.has(INTENT_IDS[0] as string) ? -Infinity : logits[0]
  for (let i = 1; i < INTENT_IDS.length; i++) {
    const v = masked.has(INTENT_IDS[i] as string) ? -Infinity : logits[i]
    if (v > bestV) {
      bestV = v
      best = i
    }
  }
  for (let i = 0; i < 8; i++) if (bestV === -Infinity) best = i // 全掩码兜底（不会发生）
  return best
}

function nearestEnemy(world: World): Tank | null {
  const p = world.player
  if (!p) return null
  let best: Tank | null = null
  let bestD = Infinity
  for (const t of world.tanks) {
    if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
    const d = Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
    if (d < bestD) {
      bestD = d
      best = t
    }
  }
  return best
}

/** 返回朝向 (tx,ty) 的主轴方向（Battle City 惯性：主轴优先）。 */
function dirToward(src: Tank, tx: number, ty: number): Direction | null {
  const dx = tx - (src.x + src.w / 2)
  const dy = ty - (src.y + src.h / 2)
  if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return null // 基本重叠
  if (Math.abs(dx) >= Math.abs(dy)) return dx > 0 ? 'right' : 'left'
  return dy > 0 ? 'down' : 'up'
}

export interface IntentPlayerOptions {
  /** 意图权重 JSON 文本（M5 后为训练产物；M4 阶段测试注入随机权重/golden）。 */
  weightsText: string
  replanEvery?: number
}

/**
 * IntentPlayer — InputLike（与 NNInput 同级；Input 提供者，永不改 World）。
 * 行为 telemetry（lastIntentId / lastSlots）为观察量，reset() 清空。
 */
export class IntentPlayer implements InputLike {
  private world: World
  private model: IntentModelLike
  private encoder = new ObsEncoder()
  private replanEvery: number

  // 注入态（tagger 同语义：纯观察）
  private prevIntentId = -1 // -1 = 无前意图（zero-vector）
  private duration = 0

  // 提交输出（窗口内保持）
  private moveDir: Direction | null = null
  private firing = false
  private thought = false

  // telemetry（读用途；不进 World）
  readonly lastIntentId: number[] = []
  readonly lastTargetCol: number[] = []

  constructor(world: World, opts: IntentPlayerOptions) {
    this.world = world
    this.model = buildIntentModelFromText(opts.weightsText)
    this.replanEvery = opts.replanEvery ?? INTENT_REPLAN_TICKS
  }

  getMoveDirection(): Direction | null {
    if (!this.thought) this.decide()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.thought) this.decide()
    return this.firing
  }

  wasItemPressed(): false {
    return false // AI 不使用主动道具（v2 口径）
  }

  endFrame(): void {
    this.thought = false
  }

  reset(): void {
    this.thought = false
    this.prevIntentId = -1
    this.duration = 0
    this.lastIntentId.length = 0
    this.lastTargetCol.length = 0
  }

  /** 每 replan 窗口推理 + 最小执行器分派（读 World，零 RNG）。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame
    if (f % this.replanEvery !== 0) return // 窗口内保持上次输出

    this.encoder.encode(this.world)
    const inject = new Float32Array(9)
    if (this.prevIntentId >= 0) inject[this.prevIntentId] = 1
    inject[8] = Math.min(this.duration, 300) / 300 // DURATION_MAX_TICKS 归一化
    this.model.intentForward(this.encoder.obs, this.encoder.scalars, inject)

    const intentIdx = argmaxLogits(this.model.intentLogits, MASKED_INTENTS)
    // 注入态推进（与 tagger 相同语义）。
    if (this.prevIntentId === intentIdx) this.duration++
    else {
      this.prevIntentId = intentIdx
      this.duration = 1
    }

    const intent = INTENT_IDS[intentIdx] as string
    this.exec(intent)
  }

  /** 3 意图最小执行器原语（stub 界定见文件头）。 */
  private exec(intent: string): void {
    const world = this.world
    const p = world.player
    if (!p || !p.alive || p.spawnTimer > 0) {
      this.moveDir = null
      this.firing = false
      this.lastIntentId.push(-1)
      return
    }
    switch (intent) {
      case 'RETURN_DEFENSE': {
        const b = BASE_POS
        this.moveDir = dirToward(p, b.col * 16 + 16, b.row * 16 + 16)
        this.firing = this.aimAlignedFire()
        break
      }
      case 'INTERCEPT': {
        // 基地威胁敌优先（威胁半径 ≈ 12 格，与 divergence-probe 同款口径）。
        let target: Tank | null = null
        let bestD = Infinity
        for (const t of world.tanks) {
          if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
          const dc = Math.abs(Math.floor((t.x + 16) / 16) - BASE_POS.col)
          const dr = Math.abs(Math.floor((t.y + 16) / 16) - BASE_POS.row)
          if (dc + dr <= 12) {
            const d = Math.abs(t.x - p.x) + Math.abs(t.y - p.y)
            if (d < bestD) {
              bestD = d
              target = t
            }
          }
        }
        const b = BASE_POS
        const cx = target ? target.x + target.w / 2 : b.col * 16 + 16
        const cy = target ? target.y + target.h / 2 : b.row * 16 + 16
        this.moveDir = dirToward(p, cx, cy)
        this.firing = this.aimAlignedFire()
        break
      }
      case 'HUNT':
      default: {
        const e = nearestEnemy(world)
        if (e) {
          this.moveDir = dirToward(p, e.x + e.w / 2, e.y + e.h / 2)
          this.firing = this.aimAlignedFire()
        } else {
          this.moveDir = null
          this.firing = false
        }
      }
    }
    this.lastIntentId.push(this.prevIntentId)
    void MAX_ENEMIES_ALIVE
  }

  /** 对齐（同行/列）即开火——stub 规则，M6 执行器接管 FireControl 语义。 */
  private aimAlignedFire(): boolean {
    const p = this.world.player
    if (!p || this.moveDir === null) return false
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    for (const t of this.world.tanks) {
      if (!t.alive || t.isPlayer || t.spawnTimer > 0) continue
      const tx = t.x + t.w / 2
      const ty = t.y + t.h / 2
      const aligned = Math.abs(tx - pcx) < 24 || Math.abs(ty - pcy) < 24
      if (!aligned) continue
      // 主轴与瞄准方向一致才开火（防止侧向扫射 stub 浪费）。
      if (this.moveDir === 'left' && tx < pcx) return true
      if (this.moveDir === 'right' && tx > pcx) return true
      if (this.moveDir === 'up' && ty < pcy) return true
      if (this.moveDir === 'down' && ty > pcy) return true
    }
    return false
  }
}
