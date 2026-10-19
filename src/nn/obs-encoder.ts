/**
 * obs-encoder.ts — observation encoder (plan §1, NN-M0a).
 *
 * SINGLE SOURCE OF TRUTH for the NN observation. Both the bun exporter
 * (`tools/replay/export-observations.ts`) and the browser runtime inference
 * (`src/nn/infer.ts`) import this module, so the bytes are guaranteed
 * identical (plan §1.4). The Python trainer NEVER re-encodes — it only
 * consumes the exported npy shards.
 *
 * Invariants (plan §1.4 / AGENTS §2.2):
 *   * Deterministic: same World + same tick -> identical obs bytes.
 *   * Read-only: never mutates the World. Never consumes world.rng.
 *   * No per-tick allocation on the hot path (reused buffers).
 *   * Does NOT read the NN `held` slots (nnMoveHeld/...); those live on the
 *     World but are Input-private state (plan §1.3-5, nn2 N4).
 *
 * Keep this file in lock-step with `nn-training/common/schema.py`. Any channel /
 * scalar / action change bumps OBS_SCHEMA_MAJOR and forces a full re-export.
 */

import {
  GRID,
  CELL,
  TANK,
  BASE_POS,
  ENEMY_SPAWNS,
  TICK_MS,
  MAX_ENEMIES_ALIVE,
  EMP_DURATION_MS,
  POWERUP_DURATION_MS,
  POWERUP_TIMEOUT_MS,
  RESPAWN_SHIELD_MS,
  Direction,
} from '../constants'
import type { World } from '../game/World'
import type { PowerUpType, Tank } from '../types'
import {
  RING_CELLS,
  ticksUntilFire,
  ticksUntilLegalTurn,
  killAssessment,
  enemyDeadline,
} from '../ai/god/ThreatBudget'
import { threatOnsetEdge } from './decision-gate'
import {
  POLICY_EXTRA_DIM,
  POLICY_EXTRA_HIT_NONE,
  POLICY_EXTRA_MIRROR_NEG,
  POLICY_EXTRA_MIRROR_SWAPS,
  POLICY_EXTRA_NAMES,
  computePolicyExtra,
} from './policy-extra'
import { cornerAnchorDist, nearestPickupCenter, sampleBasePressure } from './danger-metrics'

// ---- Canonical dimensions (mirror nn-training/common/schema.py) ----
export const OBS_CHANNELS = 16
export const BOARD = GRID // 26
// v5（plan/obs-v5-vector-gap.plan.md，2026-10-12）：30 → 40（s30..s39 追加）。
// **0–29 的序号与语义一字不动**（只追加）——旧权重/旧语料按 MAJOR 被拒收，不靠重编号兼容。
export const SCALAR_DIM = 40
// v4（plan/policy-spatial-head.plan.md §4-S0b，2026-10-05）：新增 POLICY_EXTRA(9) 张量
// （观测流新增一张量 ⇒ 旧代码读不了）。
// v5（plan/obs-v5-vector-gap.plan.md）：POLICY_EXTRA 9→37、SCALAR_DIM 30→40，且
// critic（value 头）改吃与策略头同一份 `pf` ⇒ 旧权重（value 宽 128）与新 schema 同废。
// 指纹防语义错位、MAJOR 防版本混跑。
export const OBS_SCHEMA_MAJOR = 5

// ---- Channel index map (plan §1.1 + obs-schema-v3.plan.md v4.0 §3.2) ----
export const CH = {
  terrainBrick: 0,
  terrainSteel: 1,
  terrainWater: 2,
  terrainForest: 3,
  terrainIce: 4,
  base: 5, // eagle (=2) + ring cells (=1)
  self: 6, // player tank
  enemyBasic: 7,
  enemyFast: 8,
  enemyPower: 9,
  enemyArmor: 10,
  bullet: 11, // 混合基记法：(speedBucket<<3) + (owner<<2) + (d+1)，1..32——加法非位域
  powerup: 12, // (lifeBucket<<4) | (1+enumIndex)，1..63
  waveHeat: 13, // projected spawns in next K ticks per spawn point（N 点通用）
  hitToKill: 14, // min(9, ceil(hp/damage))——随玩家星级重算（live damage，禁缓存）
  spawning: 15, // 生成中敌倒计时 Math.round(255*clamp01(spawnTimer/1000))（uint8 规则）
} as const

// ---- PowerUpType declaration order (src/types.ts:20-38) ----
export const POWERUP_ORDER: PowerUpType[] = [
  'star',
  'bomb',
  'shield',
  'freeze',
  'tank',
  'fence',
  'boat',
  'guard',
  'frenzy',
  'sacrifice',
  'rewind',
  'repair',
  'emp',
  'decoy',
  'mine',
]
const POWERUP_ENUM = new Map<PowerUpType, number>(POWERUP_ORDER.map((p, i) => [p, i]))
const POWERUP_COUNT = POWERUP_ORDER.length // 15 (asserted below)

// ---- Enemy kind -> channel offset (relative to CH.enemyBasic) ----
const KIND_INDEX: Record<string, number> = { basic: 0, fast: 1, power: 2, armor: 3 }
// ---- Intelligence tier -> tierCode (plan §1.1 ch7-10) ----
const TIER_INDEX: Record<string, number> = {
  none: 0,
  rookie: 1,
  soldier: 2,
  veteran: 3,
  commander: 4,
}
// ---- Direction -> dirIdx (constants.ts DIR_DX/DY order) ----
const DIR_INDEX: Record<Direction, number> = { up: 0, down: 1, left: 2, right: 3 }

// Scalar indices that flip sign under mirrorX (relative-direction x-components).
// v3 (OBS_SCHEMA_MAJOR=3): 保留 15/18，新增 29（vx 冰面横向速度，s28 vy 不翻）。
// v5 (OBS_SCHEMA_MAJOR=5): 新增 33（pickupRelX；s34 pickupRelY 是 y 分量，不翻）。
export const SCALAR_X_INDICES = [15, 18, 29, 33]

/**
 * 标量名序列（**逐字镜像** nn-training/common/schema.py::SCALAR_LAYOUT 的第二元）。
 *
 * 进 SCHEMA_FINGERPRINT（hy X4 / obs spec §3.4-7）：只钉 SCALAR_DIM 挡不住「交换
 * 两个标量含义」这种漏同步（维度不变、指纹不变、golden 前向仍绿，但语义错位）。
 * 改任一端 ⇒ 指纹变 ⇒ tests/nn/schema-fingerprint.test.ts 与
 * nn-training/tests/common/test_schema_fingerprint.py 双端同红。
 */
export const SCALAR_NAMES = [
  'slack',
  'baseDeadline',
  'lives',
  'level',
  'fireProgress',
  'turnCooldownRemaining',
  'ringCompleteness',
  'enemiesOnField',
  'spawnQueueRemaining',
  'tier_none',
  'tier_rookie',
  'tier_soldier',
  'tier_veteran',
  'tier_commander',
  'nearestEnemyDist',
  'nearestEnemyRelX',
  'nearestEnemyRelY',
  'nearestBaseDist',
  'nearestBaseRelX',
  'playerHp',
  'playerShield',
  'freeze',
  'stuck',
  'boat',
  'baseHp',
  'fence',
  'score',
  'emp',
  'iceVy',
  'iceVx',
  // ---- v5 新增（obs-v5-vector-gap.plan.md §2.3；只追加，不动前 30 个）----
  'moving',
  'myBullets',
  'pickupDist',
  'pickupRelX',
  'pickupRelY',
  'wallDist',
  'cornerDist',
  'baseRelY',
  'basePressure',
  'nearestEnemyApproach',
] as const
if (SCALAR_NAMES.length !== SCALAR_DIM) {
  throw new Error(`SCALAR_NAMES must have ${SCALAR_DIM} entries, got ${SCALAR_NAMES.length}`)
}

// ---- v3 ch11 弹速档（C10）：live bulletSpeed（px/tick）→ 4 序数档 ----
// 真弹速源 = bulletSpeedCps 表（config/speed.ts baseBulletSpeedPxPerTick），
// **不是** profile.projectileSpeed（规格初稿 {40,45,50,70} 前提的勘误——projectileSpeed
// 是能力维度，与实际弹速脱钩）。modern 实测分布：armor 3.60 / power 3.80 /
// basic 4.00 / fast+player 4.20-4.60 px/tick；classic power 重弹 8.0 自然落最高档。
// 桶边界按「慢→快」序数划分，2 bit（与镜像 LUT 尺寸契约一致）。
export const BULLET_SPEED_BUCKETS_PX = [3.7, 3.9, 4.1]
export function bulletSpeedBucket(speedPxPerTick: number): number {
  let b = 0
  for (const bound of BULLET_SPEED_BUCKETS_PX) if (speedPxPerTick >= bound) b++
  return b
}

// v3 ch15 生成中敌倒计时分母（SimulationEnemies 置 1000ms）。
export const SPAWN_COUNTDOWN_MS = 1000

// ---- v5 标量分母/上限（plan §2.3）----
/** s31 `myBullets` 的计数上限（沿 `MAX_ENEMIES_ALIVE` 的计数类归一惯例）。
 *  ⚠ 3 → 2：S7 探针实测（plan §3-S7 ①）两个策略口径的 p99 均 = 1（≤ 2）⇒ 按 §2.4 的
 *  预注册规则取 /2（0/1/2 满量程；/3 会留一个几乎读不到的 1.0 档）。改回去等于让探针白跑。 */
export const MY_BULLETS_CAP = 2
/** s32 `pickupDist` 归一化分母（格；与 `reward_validation` 的 0–50 值域同源）。 */
export const PICKUP_DIST_NORM = 50
/** s35 `wallDist` 归一化分母（格；半场宽 26/2 = 13，中心格读数 ≈12/13）。 */
export const WALL_DIST_NORM = 13
/** s36 `cornerDist` 归一化分母（格；四角锚点下的切比雪夫距最大值恰为 10）。 */
export const CORNER_DIST_NORM = 10
/** s39 `nearestEnemyApproach` 归一化分母（px/tick；见 `computeNearestEnemyApproach` 尾注）。 */
export const APPROACH_SPEED_NORM = 3

/** s32–s34 的拾取中心缓冲（§14.1）。 */
const _pickupCenter = new Float64Array(2)
/** s7 的层级构成复用缓冲（§14.1：去掉了每 tick 的 `[0,0,0,0,0]` 分配）。 */
const _tierCounts = [0, 0, 0, 0, 0]

const WAVE_HEAT_TICKS = 600 // K = 600 ticks (10s), plan §1.1 ch13
/** waveHeat 轮转计数复用缓冲（§14.1：v2 为每次 encode 分配 [0,0,0]）。 */
const WAVE_HEAT_COUNTS = Array.from({ length: 8 }, () => 0)

if (POWERUP_COUNT !== 15) {
  throw new Error(`POWERUP_ORDER must have exactly 15 members, got ${POWERUP_COUNT}`)
}

function clamp01(x: number): number {
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/**
 * Observation encoder. Reuses its internal obs (Uint8 16*26*26) and scalar
 * (Float32 30) buffers across calls — the caller must COPY out what it needs
 * (the exporter does, into the npy shard).
 *
 * v4：`extra`（Float32 9）与 obs/scalars 同生命周期同契约——`encode()` 一并填好，
 * 消费方（导出器 / 模型）拷走；旧布局权重不读它（纯增量、零破坏）。
 */
export class ObsEncoder {
  readonly obs: Uint8Array = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
  readonly scalars: Float32Array = new Float32Array(SCALAR_DIM)
  readonly extra: Float32Array = new Float32Array(POLICY_EXTRA_DIM)

  encode(world: World): void {
    this.obs.fill(0)
    this.scalars.fill(0)
    const hasBase = world.tileMap.hasBase()
    this.encodeTerrain(world)
    this.encodeBase(world, hasBase)
    this.encodeSelf(world)
    this.encodeEnemies(world)
    this.encodeBullets(world)
    this.encodePowerups(world)
    this.encodeWaveHeat(world)
    this.encodeScalars(world, hasBase)
    computePolicyExtra(world, this.extra)
  }

  // ---- spatial channels ----

  private setCell(ch: number, col: number, row: number, val: number): void {
    if (col < 0 || col >= BOARD || row < 0 || row >= BOARD) return
    this.obs[ch * BOARD * BOARD + row * BOARD + col] = val
  }

  /** Write `val` into every cell the (x,y,w,h) box covers (plan §1.1 格锚点). */
  private writeBox(ch: number, x: number, y: number, w: number, h: number, val: number): void {
    const c0 = Math.floor(x / CELL)
    const r0 = Math.floor(y / CELL)
    const c1 = Math.floor((x + w - 1) / CELL)
    const r1 = Math.floor((y + h - 1) / CELL)
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) this.setCell(ch, c, r, val)
  }

  private encodeTerrain(world: World): void {
    const tm = world.tileMap.grid
    for (let r = 0; r < BOARD; r++) {
      const row = tm[r]
      for (let c = 0; c < BOARD; c++) {
        const t = row[c]
        switch (t) {
          case 'brick':
            this.setCell(CH.terrainBrick, c, r, 1)
            break
          case 'steel':
            this.setCell(CH.terrainSteel, c, r, 1)
            break
          case 'water':
            this.setCell(CH.terrainWater, c, r, 1)
            break
          case 'forest':
            this.setCell(CH.terrainForest, c, r, 1)
            break
          case 'ice':
            this.setCell(CH.terrainIce, c, r, 1)
            break
          // 'base' and 'empty' -> 0 in these 5 channels (ch5 handles base)
        }
      }
    }
  }

  /**
   * 幻影基地修复（plan/goal-nn-action.md §3.2 / 卡 A0a）：无基地 arena 上
   * TileMap 的 guard 明说"无基地不算被摧毁"，旧实现却无条件在 BASE_POS 画鹰、
   * 画 ring、算 baseDeadline/基地相对标量——整条课程梯子会把它们训成
   * "常量、可忽略"，到 S4a 出现真基地时突然变成生死信号。现在无基地 ⇒
   * ch5 全 0 + s1/s6/s17/s18 全 0（"无基地"= 0，与 ch5 同语义；shape 不变）。
   * 有基地场行为逐字节不变。
   */
  private encodeBase(world: World, hasBase: boolean): void {
    if (!hasBase) return
    const bc = BASE_POS.col
    const br = BASE_POS.row
    // Eagle cell: 2 if still alive, 0 if destroyed.
    this.setCell(CH.base, bc, br, world.tileMap.isBaseDestroyed() ? 0 : 2)
    // Ring cells: 1 if the protective brick/steel is still present.
    for (const cell of RING_CELLS) {
      const t = world.tileMap.get(cell.col, cell.row)
      if (t === 'brick' || t === 'steel') this.setCell(CH.base, cell.col, cell.row, 1)
    }
  }

  private encodeSelf(world: World): void {
    const p = world.player
    if (!p || !p.alive) return
    const star = Math.min(p.level ?? 0, 3) // plan §1.1 ch6: truncate star at 3
    const d = DIR_INDEX[p.dir]
    const val = (star << 3) | (d + 1) // range 1-28
    this.writeBox(CH.self, p.x, p.y, TANK, TANK, val)
  }

  private encodeEnemies(world: World): void {
    // 敌车两态分编码（v3）：激活敌 → ch7-10（bonus<<6 + tier<<3 + dir）+ ch14
    //（剩余命中数）；生成中敌（spawnTimer>0，v2 直接 continue 跳过 = C6 缺口）→ ch15
    // 倒计时幅值。
    // ch14 的分母 = live player.damage（随星级变，combat.ts:334-355）——整轮只读一次
    //（§14：禁 per-enemy 属性查找）；无玩家/伤害非正 ⇒ 0（域外安全）。
    const playerDamage = world.player?.damage ?? 0
    for (const t of world.tanks) {
      if (!t.alive) continue
      if (t.allegiance !== 'enemy') continue
      if (t.spawnTimer > 0) {
        // C6：生成中敌人可见 + 「还有多久激活」倒计时。uint8 规则（hy E1）：幅值
        // 必须 ×255 取整后写入，clamp01 浮点直写会截断成恒 0。幅值 mirror 无关；
        // 不编码 kind/朝向（出生闪灯阶段人类也只看到位置与闪烁）。
        const cd = Math.round(255 * clamp01(t.spawnTimer / SPAWN_COUNTDOWN_MS))
        this.writeBox(CH.spawning, t.x, t.y, TANK, TANK, cd)
        continue
      }
      const kindOffset = KIND_INDEX[t.kind]
      if (kindOffset === undefined) continue
      const tier = TIER_INDEX[t.aiState?.level ?? 'none'] ?? 0
      const d = DIR_INDEX[t.dir]
      // C8 奖励车位在 bit6（v3.2 A1：bit4 与 tier 字段逐位碰撞——tier≥2 时
      // tier<<3 占 bit4）。加法组装；mirror 只翻低 3 位，bit6 自动保留。
      const bonus = t.bonus ? 1 : 0
      const val = (bonus << 6) + (tier << 3) + (d + 1) // 1..100
      this.writeBox(CH.enemyBasic + kindOffset, t.x, t.y, TANK, TANK, val)
      // C5 ch14：敌剩余命中数 = min(9, ceil(enemy.hp / player.damage))——每帧
      // 从 live 伤害现算、禁缓存（obs spec §3.2 ch14；所有兵种，非 armor 专利）。
      if (playerDamage > 0) {
        const hits = Math.min(9, Math.ceil((t.hp ?? 0) / playerDamage))
        this.writeBox(CH.hitToKill, t.x, t.y, TANK, TANK, hits)
      }
    }
  }

  private encodeBullets(world: World): void {
    for (const b of world.bullets) {
      if (!b.alive) continue
      if (b.allegiance === 'ally') continue // plan nn3 N7: ignore ally bullets
      const d = DIR_INDEX[b.dir]
      const owner = b.allegiance === 'enemy' ? 0 : 1
      // C10 弹速档：live Bullet.speed（px/tick）→ 4 序数档（桶边界见模块头）。
      // 加法编码（dsf A2）：d+1=4 占 bit2 与 owner<<2 重叠，字面 OR 会令
      // player-right ≡ enemy-right——**全程加法**，混合基（位权 8/4 + 加法）非位域。
      const sb = bulletSpeedBucket(b.speed)
      const val = (sb << 3) + (owner << 2) + (d + 1) // 1..32
      // Bullet is ~6px: write its CENTER cell (plan §1.1 格锚点).
      const cc = Math.floor((b.x + b.w / 2) / CELL)
      const cr = Math.floor((b.y + b.h / 2) / CELL)
      this.setCell(CH.bullet, cc, cr, val)
    }
  }

  private encodePowerups(world: World): void {
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      const idx = POWERUP_ENUM.get(pu.type)
      if (idx === undefined) continue
      // C9 剩余寿命档（dsf 复核无冲突：typeIdx+1≤15 占位 0-3，lifeBucket 占位 4-5）：
      // 新刷=3 → 将消失=0（2 bit 四档）。
      const remaining = POWERUP_TIMEOUT_MS - pu.lifeTimer
      const lifeBucket = Math.min(3, Math.floor(Math.max(0, remaining) / 5000))
      const val = (lifeBucket << 4) | (idx + 1) // 1..63
      const c = Math.floor(pu.x / CELL)
      const r = Math.floor(pu.y / CELL)
      this.setCell(CH.powerup, c, r, val)
    }
  }

  private encodeWaveHeat(world: World): void {
    // Projected spawns in the next K=600 ticks (plan §1.1 ch13). v3：**N 点通用**
    // （ms F1 勘误——v2 硬编码 3 点而 arena 是 4 点，第 4 点热量丢失且轮转错位）。
    // Approximation (documented): bounded by the spawn INTERVAL (not the raw
    // queue length, which overestimates), capped by enemies still unspawned.
    const remaining = world.spawnQueue.length
    const intervalMs = world.rules?.spawnIntervalMs ?? 1500
    const projK = Math.floor((WAVE_HEAT_TICKS * TICK_MS) / intervalMs)
    const proj = Math.max(0, Math.min(remaining, projK))
    const points = world.enemySpawnPoints
    const n = points.length
    if (n === 0) return
    const counts = WAVE_HEAT_COUNTS // §14.1：模块级复用缓冲，禁 per-tick 分配
    counts.fill(0, 0, n)
    for (let i = 0; i < proj; i++) counts[i % n]++
    for (let i = 0; i < n; i++) {
      const px = points[i]?.x ?? ENEMY_SPAWNS[i].col * CELL
      const py = points[i]?.y ?? ENEMY_SPAWNS[i].row * CELL
      this.setCell(CH.waveHeat, Math.floor(px / CELL), Math.floor(py / CELL), counts[i])
    }
  }

  // ---- scalar vector (plan §1.2) ----

  private encodeScalars(world: World, hasBase: boolean): void {
    const s = this.scalars
    const p = world.player
    const FIELD_DIAG = 26 * CELL * 1.5

    // slack / baseDeadline via ThreatBudget (reused, plan §1.2).
    // obs-v5（§14.1/§14.6）：`world.tanks.filter(...)` 的**每 tick 分配**去掉——
    // `world.allTanks` 取一次传引用（玩家族/基地族/extra 共用同一份），谓词内联。
    const allTanks = world.allTanks
    let minKillSlack = Infinity
    let minBaseDeadline = Infinity
    let enemyCount = 0
    for (let i = 0; i < allTanks.length; i++) {
      const e = allTanks[i]
      if (!e.alive || e.allegiance !== 'enemy' || e.spawnTimer > 0) continue
      enemyCount++
      const ka = killAssessmentSlack(world, p, e)
      if (ka.killSlack < minKillSlack) minKillSlack = ka.killSlack
      if (ka.baseDeadline < minBaseDeadline) minBaseDeadline = ka.baseDeadline
    }
    s[0] = enemyCount === 0 ? 1 : clamp01(minKillSlack / 600)
    // 幻影基地修复（卡 A0a）：无基地 ⇒ baseDeadline 标量恒 0（"无基地"= 0，
    // 与 ch5 同语义），不写 ThreatBudget 对不存在基地算出的幽灵值。
    s[1] = !hasBase ? 0 : enemyCount === 0 ? 1 : clamp01(minBaseDeadline / 600)

    // lives / level
    s[2] = clamp01(world.lives / 3)
    s[3] = clamp01(Math.min(p?.level ?? 0, 3) / 3)

    // fire-control progress
    if (p && p.nextFireInterval > 0) {
      const now = world.frame * TICK_MS
      s[4] = clamp01((now - (p.lastFire ?? 0)) / p.nextFireInterval)
    } else {
      s[4] = 0
    }

    // turn-cooldown remaining
    const cd = world.rules?.turnCooldownMs ?? 0
    if (p && cd > 0) {
      const now = world.frame * TICK_MS
      const elapsed = now - (p.lastTurnMs ?? -9999)
      s[5] = clamp01((cd - elapsed) / cd)
    } else {
      s[5] = 0
    }

    // ring completeness（无基地 ⇒ 恒 0，且跳过 RING_CELLS 扫描）
    if (!hasBase) {
      s[6] = 0
    } else {
      let intact = 0
      for (const cell of RING_CELLS) {
        const t = world.tileMap.get(cell.col, cell.row)
        if (t === 'brick' || t === 'steel') intact++
      }
      s[6] = intact / RING_CELLS.length
    }

    // enemies on field / spawn queue remaining
    s[7] = clamp01(world.enemyCount / MAX_ENEMIES_ALIVE)
    s[8] = clamp01(world.spawnQueue.length / Math.max(1, world.enemiesTotal))

    // tier composition
    const tierCounts = _tierCounts
    tierCounts[0] = 0
    tierCounts[1] = 0
    tierCounts[2] = 0
    tierCounts[3] = 0
    tierCounts[4] = 0
    for (let i = 0; i < allTanks.length; i++) {
      const e = allTanks[i]
      if (!e.alive || e.allegiance !== 'enemy' || e.spawnTimer > 0) continue
      const ti = TIER_INDEX[e.aiState?.level ?? 'none'] ?? 0
      tierCounts[ti]++
    }
    const denom = Math.max(1, enemyCount)
    for (let i = 0; i < 5; i++) s[9 + i] = tierCounts[i] / denom

    // nearest enemy relative (dist, dx/dist, dy/dist)
    // obs-v5：`pc` 的每 tick 对象分配去掉（纯局部标量）；并顺带记录该敌的 live 速度与
    // 中心（s39 `nearestEnemyApproach` 与 s14 **同一个敌**：同循环、同 hypot 度量、
    // `strict <` 首个最小者胜 —— 分离成两趟就会在平局拍上选中不同敌）。
    const hasPc = p !== null
    const pcx = p ? p.x + p.w / 2 : 0
    const pcy = p ? p.y + p.h / 2 : 0
    let nd = Infinity
    let ndx = 0
    let ndy = 0
    let nvx = 0
    let nvy = 0
    if (hasPc) {
      for (let i = 0; i < allTanks.length; i++) {
        const e = allTanks[i]
        if (!e.alive || e.allegiance !== 'enemy' || e.spawnTimer > 0) continue
        const ex = e.x + e.w / 2
        const ey = e.y + e.h / 2
        const dx = ex - pcx
        const dy = ey - pcy
        const d = Math.hypot(dx, dy)
        if (d < nd) {
          nd = d
          ndx = dx
          ndy = dy
          nvx = e.vx
          nvy = e.vy
        }
      }
    }
    if (hasPc && nd < Infinity) {
      s[14] = clamp01(nd / FIELD_DIAG)
      s[15] = ndx / nd
      s[16] = ndy / nd
    } else {
      s[14] = 0
      s[15] = 0
      s[16] = 0
    }

    // nearest base relative（无基地 ⇒ 恒 0；有基地时才是真基地信号）
    if (!hasBase) {
      s[17] = 0
      s[18] = 0
      s[37] = 0 // 与有基地分支同值：s37 是 s18 的 y 分量，无基地时同样归 0（不依赖 encode() 的 fill）
    } else {
      const bcx = BASE_POS.col * CELL + CELL / 2
      const bcy = BASE_POS.row * CELL + CELL / 2
      if (hasPc) {
        const dx = bcx - pcx
        const dy = bcy - pcy
        const d = Math.hypot(dx, dy) || 1
        s[17] = clamp01(d / FIELD_DIAG)
        s[18] = dx / d
        s[37] = dy / d // v5：补 s18 缺掉的 y 分量（同一 d，同一分支）
      } else {
        s[17] = 0
        s[18] = 0
        s[37] = 0
      }
    }

    // ---- v3 新增 s19..s29（obs-schema-v3.plan.md v4.0 §3.3 定稿）----

    // s19 玩家 HP（ratio 定案：raw≈263 量纲离群；maxHp 阶段内恒定 ⇒ 与 raw 信息等价）
    s[19] = p ? clamp01(p.hp / p.maxHp) : 0
    // s20 玩家无敌盾：道具盾会把 shieldTimer 拉到 20000ms —— 先 min 到重生盾窗口
    // 再归一，防瞬间饱和（C2）
    s[20] = p ? clamp01(Math.min(p.shieldTimer ?? 0, RESPAWN_SHIELD_MS) / RESPAWN_SHIELD_MS) : 0
    // s21 敌冰冻（world 级计时器，可叠加，POWERUP_DURATION_MS=20000）
    s[21] = clamp01(world.freezeTimer / POWERUP_DURATION_MS)
    // s22 卡死计时（World.stuckTicks 由 Simulation 末尾维护，与导出器同源 stuck-detect.ts）
    s[22] = clamp01(world.stuckTicks / 900) // 分母 = reward 封顶（c4-margin.jsonc:53）
    // s23 水陆两栖 active（C7，classic 预留）
    s[23] = p && (p.boatTimer ?? 0) > 0 ? 1 : 0
    // s24 base HP —— **无条件编码**（v3.1 定案：baseHp/baseMaxHp 无基地关恒置满
    // 1.0 不变化，World.ts:484-485；恒 0 会与「基地被毁=0」碰撞）
    s[24] = clamp01(world.baseHp / world.baseMaxHp)
    // s25 fence 钢圈 active（A2；undefined = 无）
    s[25] = world.fenceExpireFrame !== undefined ? 1 : 0
    // s26 分数（全难度计分非恒 0；基准 20000 档避开 5000 倍频混淆）
    s[26] = clamp01(world.score / 20000)
    // s27 EMP 静默剩余（§8.6 定案并入；EMP_DURATION_MS=8000）
    s[27] = clamp01(world.empTimer / EMP_DURATION_MS)
    // s28/s29 冰面速度分量（vx/vy，px/tick / 玩家速度 ⇒ [-1,1]；普通地面恒 ±1 或 0，
    // 冰面 glide 才携带隐藏态）。vx 是 x 方向分量 ⇒ SCALAR_X_INDICES 含 29。
    if (p && p.speed > 0) {
      s[28] = p.vy / p.speed
      s[29] = p.vx / p.speed
    } else {
      s[28] = 0
      s[29] = 0
    }

    // ---- v5 新增 s30..s39（plan/obs-v5-vector-gap.plan.md §2.3 冻结）----

    // s30 「这一拍在推」：顶墙推 vs STOP 的区分位（冰面滑行反例 ⇒ s28/s29 盖不住，保留）。
    s[30] = p && p.moving ? 1 : 0

    // s31 玩家本人在飞弹数（`allegiance==='player'`；`'ally'` 是友军弹，不算）。
    // 武器通道 ch11 把玩家弹与友军弹记成同一 owner 位（owner = allegiance==='enemy' ? 0 : 1）
    // ⇒ 这一列补的是那张通道的 GAP（appendix A 的 S14 根因）。
    let myBullets = 0
    const bullets = world.bullets
    for (let i = 0; i < bullets.length; i++) {
      const b = bullets[i]
      if (b.alive && b.allegiance === 'player') myBullets++
    }
    s[31] = Math.min(myBullets, MY_BULLETS_CAP) / MY_BULLETS_CAP

    // s32–s34 拾取三连：距离换算 `!alive?0 : raw<0?1.5:raw/50`（玩家死亡取 0，跟随
    // extra 的「无玩家全 0」纪律）；向量按**像素中心欧氏**（与 s15/s16 同规）。
    if (p && p.alive) {
      const raw = nearestPickupCenter(world, _pickupCenter)
      s[32] = raw < 0 ? POLICY_EXTRA_HIT_NONE : clamp01(raw / PICKUP_DIST_NORM)
      if (raw >= 0) {
        const dx = _pickupCenter[0] - (p.x + p.w / 2)
        const dy = _pickupCenter[1] - (p.y + p.h / 2)
        const d = Math.hypot(dx, dy)
        if (d > 0) {
          s[33] = dx / d
          s[34] = dy / d
        }
      }
    } else {
      s[32] = 0
      s[33] = 0
      s[34] = 0
    }

    // s35/s36 空间余量：到边界格距 /13、到角锚点切比雪夫距 /10（复用 `cornerAnchorDist`，
    // 与 `cornerWeightTick` 同一份四角几何，不给观测侧另建公式）。
    if (p) {
      const c = Math.floor((p.x + p.w / 2) / CELL)
      const r = Math.floor((p.y + p.h / 2) / CELL)
      const toEdge = Math.min(c, r, BOARD - 1 - c, BOARD - 1 - r)
      s[35] = clamp01(toEdge / WALL_DIST_NORM)
      s[36] = clamp01(cornerAnchorDist(c, r) / CORNER_DIST_NORM)
    }

    // s38 基地压力：`sampleBasePressure(world, enemiesOnly=true)` —— telemetry 那份公式的
    // **同一实现**，只多一道敌车过滤（否则玩家自己贴家会把读数顶满，plan §2.5）。
    s[38] = hasBase ? sampleBasePressure(world, true) : 0

    // s39 最近敌人**径向接近速度**（正 = 朝我来）。分母 3 与 clamp 的说明见模块常量。
    s[39] = computeNearestEnemyApproach(nd, ndx, ndy, nvx, nvy)
  }
}

/**
 * s39 `nearestEnemyApproach`：`−(V·u)/3`，clamp [−1,1]。
 *
 * `u = (E−P)/|E−P|`（单位向量，玩家 → 最近敌）；`V = (vx,vy)` 是该敌的 live 速度（px/tick，
 * 轴锁 ⇒ 只有一轴非零）。敌军速度恒为正朝向自身移动方向 ⇒ 向玩家逼近时 `V·u < 0`，
 * 取负后为正。
 *
 * 分母 3 是**刻意留余量**（不是满量程归一）：各兵种 live 速度 ≤ 1.25 px/tick
 * （`config/speed.ts`：basic 1.00 / fast 1.20 / 玩家 3★ 1.25），故读数峰值 ≈0.40——
 * 换难度或未来提速都不会越界。局限（已知，不加维）：只看最近一个、只看径向
 * （纯绕侧的切向接近读 0，罕见）。
 */
function computeNearestEnemyApproach(
  nd: number,
  ndx: number,
  ndy: number,
  nvx: number,
  nvy: number,
): number {
  if (!isFinite(nd) || nd === 0) return 0
  const ux = ndx / nd
  const uy = ndy / nd
  const v = -(nvx * ux + nvy * uy) / APPROACH_SPEED_NORM
  return v < -1 ? -1 : v > 1 ? 1 : v
}

/**
 * TS 侧 mirrorX 的 **scalars 半边**（plan §3-S3）：对 `SCALAR_X_INDICES` 的 x 分量取负。
 *
 * 与 `nn-training/worker/data/mirror.py::mirror_x` 的第 2 个返回值同表同序；extra 半边是
 * `policy-extra.ts::mirrorSwap`。两者合起来 = python `mirror_x` 的向量部分。
 * 原地改写（`scalars` 由调用方拥有）。用途 = A4/A4b 双端对账（生产增广在 python）。
 */
export function mirrorScalars(scalars: Float32Array): void {
  for (let i = 0; i < SCALAR_X_INDICES.length; i++) {
    const k = SCALAR_X_INDICES[i]
    scalars[k] = -scalars[k]
  }
}

// ---------------------------------------------------------------
// ThreatBudget-derived scalars (reused, no duplication). Thin wrappers
// around ThreatBudget so the encoder stays a pure function of (world).
// ---------------------------------------------------------------

function killAssessmentSlack(world: World, p: Tank | null, e: Tank) {
  if (!p) return { killSlack: Infinity, baseDeadline: Infinity }
  const ka = killAssessment(world, p, e)
  const dl = enemyDeadline(world, e)
  return { killSlack: ka.killSlack, baseDeadline: dl.enemyDamageDeadline }
}

// ---------------------------------------------------------------
// Decision-tick predicate (plan §1.3) + action/mask helpers.
// Exported so the exporter AND the unit tests share one implementation.
// ---------------------------------------------------------------

/** Fire-control EDGE: the tick the cooldown just elapsed (plan §1.3, nn3 N1). */
export function isFireEdge(world: World): boolean {
  const p = world.player
  if (!p) return false
  const iv = p.nextFireInterval ?? 0
  if (!(iv > 0)) return false
  const now = world.frame * TICK_MS
  const last = p.lastFire ?? -9999
  const ready = now - last >= iv
  const prevReady = now - TICK_MS - last >= iv
  return ready && !prevReady
}

/**
 * Event-type decision tick (plan §1.3).
 *   turn-event  : direction changed vs previous frame
 *   fireEdge    : cooldown just elapsed
 *   item-event  : guard/frenzy bit changed vs previous frame
 *   threatOnset : entered an enemy bullet/barrel lane (edge; x2 rung — see below)
 *   subsample   : t % k === 0  (k=10) — keeps "do nothing" negative samples
 * Priority for `condition`: turn(0) > fire(1) > item(2) > threat(4) > subsample(3).
 *
 * threat-ONSET（plan/new-era-stop.plan.md §6 R2.1）：`condition === 4` 只在调用方传入
 * `prevThreat` 时可能产出——**沿，不是电平**（电平在威胁持续期每 tick 成立 ⇒ Δt=1 ⇒
 * 部署 60Hz + 视界塌缩，hy P0-3）。`prevThreat` 必须由调用方**逐 tick**维护（上一 tick
 * 的 `inThreatLane`）；不传 = 旧四类事件，现有 label 导出器逐字节不变（是否给 BC 语料
 * 开事件属 x2 rung 的排产决定，不在 R2 内）。RL 侧的事件门走 `decision-gate.ts::decisionDue`
 * （同一 `threatOnsetEdge` 定义，含最小间隔闸与 state-init 抑制窗）。
 */
export function decisionTick(
  t: number,
  world: World,
  prevDir: Direction | null,
  curDir: Direction | null,
  prevGuard: boolean,
  curGuard: boolean,
  prevFrenzy: boolean,
  curFrenzy: boolean,
  k = 10,
  prevThreat?: boolean,
): { isDecision: boolean; condition: number } {
  const turnEvent = prevDir !== null && prevDir !== curDir
  const fireEdge = isFireEdge(world)
  const itemEvent = prevGuard !== curGuard || prevFrenzy !== curFrenzy
  const threatOnset = prevThreat === undefined ? false : threatOnsetEdge(prevThreat, world)
  const subsample = t % k === 0
  const isDecision = turnEvent || fireEdge || itemEvent || threatOnset || subsample
  let condition = 3
  if (turnEvent) condition = 0
  else if (fireEdge) condition = 1
  else if (itemEvent) condition = 2
  else if (threatOnset) condition = 4
  return { isDecision, condition }
}

export interface FrameLabel {
  move: number // 0 none,1 up,2 down,3 left,4 right
  fire: number // 0 release,1 hold
}

/** Map a packed human input frame to the 2 action-head labels (v2: no item head). */
export function actionFromFrame(f: { direction: Direction | null; firing: boolean }): FrameLabel {
  const move = f.direction ? DIR_INDEX[f.direction] + 1 : 0
  const fire = f.firing ? 1 : 0
  return { move, fire }
}

export interface Masks {
  move: number[] // legacy length 5 (0=STOP); v6 length 4 (up/down/left/right)
  fire: number[] // legacy length 2 ([no-shot, shot-ready]); v6 length 3 (\u0031 before-turn / after-turn)
  /** B1 v6: hold head mask (always [1,1]); legacy consumers ignore it. */
  hold: number[] // length 2
}

/**
 * Invalid-action mask (plan §1.3-4), generated from World state.
 *   fire : hold-mask valid only when cooldown elapsed (else masked).
 *   move : v1 = all valid (turn-lock refinement deferred; noted in plan).
 * v2: item head removed — guard/frenzy (and their masks) no longer exist.
 */
/** 动作合法性掩码（7 位 = move[5] + fire[2]）。
 *
 *  ⚠️ **信息量提示（2026-09-14，bc-c4-v3 语料实测）**：当前动作空间里**移动永远合法**、
 *  开火只受冷却约束，所以本函数返回的是——
 *    move = [1,1,1,1,1]   恒 1（5 位全常量）
 *    fire = [1, ready?1:0] 第 1 位恒 1，**只有第 2 位（fire-ready）携带信息**
 *  ⇒ 7 位里 6 位是死信息，整段 mask 的唯一语义 = 「这一帧能不能开火」。
 *  实测语料 `masks.npy` 各位均值 `[1,1,1,1,1,1,0.374]`：最后一位 37.4% ⇒ 教师
 *  「能开火时只有 19.5% 的帧真的开火」（fire 正例 7.3%）。
 *
 *  推论：**不要指望 mask 教策略"避开非法动作"**（没有非法动作），也别把它当
 *  独立特征通道评估；学习信号只在 obs/scalars 里。若将来动作空间引入非法动作
 *  （如转向锁定），必须同时更新本函数与 `SCHEMA_FINGERPRINT` 常量表。
 *
 *  改动纪律：本函数返回的**内容**不是指纹输入（指纹只钉上方常量表），但改了
 *  mask 语义 = 改了「一个样本是什么」⇒ 必须走 schema bump 与新语料轮次。
 */
export function computeMasks(world: World, v6 = false): Masks {
  const p = world.player
  const ready = p ? isFireReady(world) : false
  // v6: fire head = {0 no shot, 1 before turn, 2 after turn}. Both shot slots
  // share the same physical gate (cooldown), so only readiness carries information
  // — same as the legacy 2-slot layout. hold/move stay constant by design.
  const fire: number[] = v6 ? [1, ready ? 1 : 0, ready ? 1 : 0] : [1, ready ? 1 : 0]
  const move: number[] = v6 ? [1, 1, 1, 1] : [1, 1, 1, 1, 1]
  const hold: number[] = [1, 1]
  return { move, fire, hold }
}

function isFireReady(world: World): boolean {
  const p = world.player
  if (!p) return false
  const iv = p.nextFireInterval ?? 0
  if (!(iv > 0)) return false
  return world.frame * TICK_MS - (p.lastFire ?? -9999) >= iv
}

export { ticksUntilFire, ticksUntilLegalTurn }

// ================================================================
// SCHEMA_FINGERPRINT（hy X4）—— 双端一致性断言的机器锚。
// 覆盖 = 决定观测字节的全部常量；任何一项变动指纹必变 ⇒ 双端单测红 ⇒ 漏同步
// 现形，并写进 npy shard manifest（数据自述其 schema）。派生公式的正确性由
// 单测另锁（见 obs spec §4 测试清单），指纹只钉「常量身份」。
// 序列化 = 显式字段序 `|`/`,` 连接（语言中立，common/schema.py 逐字对齐）。
// ================================================================

function fnv1a(str: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

export const SCHEMA_FINGERPRINT = fnv1a(
  [
    // Single point: the schema tag tracks OBS_SCHEMA_MAJOR (was a separate
    // hard-coded 'v3' literal). Same value today ⇒ fingerprint unchanged.
    `v${OBS_SCHEMA_MAJOR}`,
    OBS_SCHEMA_MAJOR,
    OBS_CHANNELS,
    SCALAR_DIM,
    BOARD,
    SCALAR_X_INDICES.join(','),
    SCALAR_NAMES.join(','), // 标量语义序列（common/schema.py SCALAR_LAYOUT 第二元同名同序）
    CH.terrainBrick,
    CH.terrainSteel,
    CH.terrainWater,
    CH.terrainForest,
    CH.terrainIce,
    CH.base,
    CH.self,
    CH.enemyBasic,
    CH.enemyFast,
    CH.enemyPower,
    CH.enemyArmor,
    CH.bullet,
    CH.powerup,
    CH.waveHeat,
    CH.hitToKill,
    CH.spawning,
    POWERUP_ORDER.join(','),
    BULLET_SPEED_BUCKETS_PX.join(','),
    SPAWN_COUNTDOWN_MS,
    WAVE_HEAT_TICKS,
    // POLICY_EXTRA 身份（v4，一项）：维度 + 语义名序 + 镜像互换对。
    // 与 common/schema.py `_FINGERPRINT_PARTS` 末项逐字同串（双端指纹测试共锚）。
    // v5：末段由三字段扩为四字段（`dim|names|swaps|neg`）——取负域与互换域分家，
    // swap 表达不了「x 分量取负」（plan §2.4 冻结）。
    [
      String(POLICY_EXTRA_DIM),
      POLICY_EXTRA_NAMES.join(','),
      POLICY_EXTRA_MIRROR_SWAPS.map(([a, b]) => `${a}:${b}`).join(','),
      POLICY_EXTRA_MIRROR_NEG.join(','),
    ].join('|'),
  ].join('|'),
)
