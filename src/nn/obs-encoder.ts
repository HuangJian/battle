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
 * Keep this file in lock-step with `nn-training/schema.py`. Any channel /
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

// ---- Canonical dimensions (mirror nn-training/schema.py) ----
export const OBS_CHANNELS = 16
export const BOARD = GRID // 26
export const SCALAR_DIM = 30
export const OBS_SCHEMA_MAJOR = 3

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
export const SCALAR_X_INDICES = [15, 18, 29]

/**
 * 标量名序列（**逐字镜像** nn-training/schema.py::SCALAR_LAYOUT 的第二元）。
 *
 * 进 SCHEMA_FINGERPRINT（hy X4 / obs spec §3.4-7）：只钉 SCALAR_DIM 挡不住「交换
 * 两个标量含义」这种漏同步（维度不变、指纹不变、golden 前向仍绿，但语义错位）。
 * 改任一端 ⇒ 指纹变 ⇒ tests/nn/schema-fingerprint.test.ts 与
 * nn-training/tests/test_schema_fingerprint.py 双端同红。
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

const WAVE_HEAT_TICKS = 600 // K = 600 ticks (10s), plan §1.1 ch13
/** waveHeat 轮转计数复用缓冲（§14.1：v2 为每次 encode 分配 [0,0,0]）。 */
const WAVE_HEAT_COUNTS = new Array<number>(8).fill(0)

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
 */
export class ObsEncoder {
  readonly obs: Uint8Array = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
  readonly scalars: Float32Array = new Float32Array(SCALAR_DIM)

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
    let minKillSlack = Infinity
    let minBaseDeadline = Infinity
    const enemies = world.tanks.filter(
      (t) => t.alive && t.allegiance === 'enemy' && t.spawnTimer <= 0,
    )
    for (const e of enemies) {
      const ka = killAssessmentSlack(world, p, e)
      if (ka.killSlack < minKillSlack) minKillSlack = ka.killSlack
      if (ka.baseDeadline < minBaseDeadline) minBaseDeadline = ka.baseDeadline
    }
    s[0] = enemies.length === 0 ? 1 : clamp01(minKillSlack / 600)
    // 幻影基地修复（卡 A0a）：无基地 ⇒ baseDeadline 标量恒 0（"无基地"= 0，
    // 与 ch5 同语义），不写 ThreatBudget 对不存在基地算出的幽灵值。
    s[1] = !hasBase ? 0 : enemies.length === 0 ? 1 : clamp01(minBaseDeadline / 600)

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
    const tierCounts = [0, 0, 0, 0, 0]
    for (const e of enemies) {
      const ti = TIER_INDEX[e.aiState?.level ?? 'none'] ?? 0
      tierCounts[ti]++
    }
    const denom = Math.max(1, enemies.length)
    for (let i = 0; i < 5; i++) s[9 + i] = tierCounts[i] / denom

    // nearest enemy relative (dist, dx/dist, dy/dist)
    const pc = p ? { x: p.x + p.w / 2, y: p.y + p.h / 2 } : null
    let nd = Infinity
    let ndx = 0
    let ndy = 0
    if (pc) {
      for (const e of enemies) {
        const ex = e.x + e.w / 2
        const ey = e.y + e.h / 2
        const dx = ex - pc.x
        const dy = ey - pc.y
        const d = Math.hypot(dx, dy)
        if (d < nd) {
          nd = d
          ndx = dx
          ndy = dy
        }
      }
    }
    if (pc && nd < Infinity) {
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
    } else {
      const bcx = BASE_POS.col * CELL + CELL / 2
      const bcy = BASE_POS.row * CELL + CELL / 2
      if (pc) {
        const dx = bcx - pc.x
        const dy = bcy - pc.y
        const d = Math.hypot(dx, dy) || 1
        s[17] = clamp01(d / FIELD_DIAG)
        s[18] = dx / d
      } else {
        s[17] = 0
        s[18] = 0
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
 *   subsample   : t % k === 0  (k=10) — keeps "do nothing" negative samples
 * Priority for `condition`: turn > fire > item > subsample.
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
): { isDecision: boolean; condition: number } {
  const turnEvent = prevDir !== null && prevDir !== curDir
  const fireEdge = isFireEdge(world)
  const itemEvent = prevGuard !== curGuard || prevFrenzy !== curFrenzy
  const subsample = t % k === 0
  const isDecision = turnEvent || fireEdge || itemEvent || subsample
  let condition = 3
  if (turnEvent) condition = 0
  else if (fireEdge) condition = 1
  else if (itemEvent) condition = 2
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
  move: number[] // length 5
  fire: number[] // length 2  ([release valid, hold valid])
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
export function computeMasks(world: World): Masks {
  const p = world.player
  const ready = p ? isFireReady(world) : false
  const fire: number[] = [1, ready ? 1 : 0]
  const move: number[] = [1, 1, 1, 1, 1]
  return { move, fire }
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
// 序列化 = 显式字段序 `|`/`,` 连接（语言中立，schema.py 逐字对齐）。
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
    'v3',
    OBS_SCHEMA_MAJOR,
    OBS_CHANNELS,
    SCALAR_DIM,
    BOARD,
    SCALAR_X_INDICES.join(','),
    SCALAR_NAMES.join(','), // 标量语义序列（schema.py SCALAR_LAYOUT 第二元同名同序）
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
  ].join('|'),
)
