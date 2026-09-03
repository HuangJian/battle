/**
 * config-stage.ts — 课程自定义关卡解码（plan/rl-training-config.md §5 / M1d）。
 *
 * 课程配置 `stages[i].grid`（stageData.ts 13×13 数字瓦格）经 fetch_task →
 * sampler-agent → `--stage-json` 到达远端本模块：
 *
 *   decodeStageGrid(stageJson, stageId) → StageData
 *
 * 复用 `src/config/stages.ts` 的 codec（decodeLevel / decodeForceString，同一
 * 26×26 瓦格输出），因此与真实关共用 `TileMap.loadStage` 消费面、渲染零差异。
 *
 * 四守卫（plan §5.2，必须全部落地）：
 *   ① 短路：`--stage-json` 存在时必须**在** `resolveArenaStage`/真实关表解析
 *     之前解码——否则 2000+i 会走到 `STAGES[2000]` 未定义 → 静默 [SKIP] 漏跑；
 *   ② index-0 显式：自定义关一律 `world.loadStageData(stage, 0)`（硬编码，
 *     不读 isArenaId 巧合）——1.05^index 关卡缩放 → 里程碑掉落内存耗尽事故
 *     （2026-08-30 本仓实测）与关卡号取值无关；
 *   ③ enemyCount = count：`tel.enemyTotal` 取 `stage.enemyCount ?? 20`，
 *     否则 progress 维度口径错；
 *   ④ dodge 强制 off：`--stage-json` 存在时 `resolveDodge` 默认落 l0 保底层
 *     会覆盖动作（isArenaId(2000+i)=true）——自定义关没有保底层语义。
 *
 * 出生点/基地邻格校验（§5.3）：grid 13×13、forces ≤20 字符、出生点落点及其
 * 邻格不得是 solid（本仓刚修过 co-op 出生点卡死，此类校验是既定教训）。
 */

import { decodeForceString, decodeLevel } from '../config/stages'
import type { StageData, TankKind } from '../types'

//: 自定义关（配置内 grid）起始 ID —— 与 `nn-training/rl/config.py::CUSTOM_STAGE_BASE`
//: 逐值一致（跨语言常量，改任一侧必须同步另一侧）。
export const CUSTOM_STAGE_BASE = 2000

export interface StageJsonSpawn {
  col: number
  row: number
}

/** 出生点变体（`spawn_variants` 单元素）：某 (stage, seed) 组合下生效的出生点。 */
export interface StageJsonSpawnVariant {
  player_spawn?: StageJsonSpawn
  enemy_spawns?: StageJsonSpawn[]
}

/** 课程配置 `stages[i]` 的 stageJson 载荷（Python 侧 `CourseConfig.stage_json` 序列化）。 */
export interface StageJson {
  name?: string
  grid: number[][]
  forces?: string
  count?: number
  player_spawn?: StageJsonSpawn
  enemy_spawns?: StageJsonSpawn[]
  /**
   * 出生点变体池（2026-09-03，p1-onset 随机出生点）：
   * `seed` 给定时按确定性哈希在池中选一；无 seed / 未提供时退回 player_spawn /
   * enemy_spawns（或 variants[0]）。用途=语料与 rollout 的几何多样性（God-AI
   * BC 语料曾因固定出生点 left 方向仅 1.4%，模型学到位置记忆而非找敌技能）。
   */
  spawn_variants?: StageJsonSpawnVariant[]
}

const SOLID: ReadonlySet<string> = new Set(['b', 's'])

/** 出生点 2×2 坦克占位不得压 solid/base/越界（co-op 卡死教训）。

    只查 2×2 占位本身的冲突——出生点与基地之间留空/贴门是合法设计（经典关
    PLAYER_SPAWN 就在基地门位），邻格限制过严会误杀门位布局。
 */
/** (seed, n) → [0, n)：确定性整数哈希（无状态、跨进程稳定——(stage,seed) 必须
    处处得到同一出生点布局，BC 语料与 RL rollout 的分布才能对齐）。 */
function pickVariantIndex(seed: number, n: number): number {
  let h = (seed ^ 0x9e3779b9) >>> 0
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b)
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b)
  h = (h ^ (h >>> 16)) >>> 0 // imul 返回 int32 可能为负——>>>0 归 uint32 再取模
  return h % n
}

function assertSpawnClear(tiles: string[], col: number, row: number, what: string): void {
  for (let dr = 0; dr < 2; dr++) {
    for (let dc = 0; dc < 2; dc++) {
      const ch = tiles[row + dr]?.[col + dc]
      if (ch === undefined) {
        throw new Error(`decodeStageGrid: ${what} 出生点 ({${col},${row}}) 超出 26×26 边界`)
      }
      if (SOLID.has(ch) || ch === 'E') {
        throw new Error(
          `decodeStageGrid: ${what} 出生点 ({${col},${row}}) 与 solid/基地重叠（${ch}）`,
        )
      }
    }
  }
}

/**
 * stageJson（字符串或已解析对象）→ StageData。**必须在 arena/真实关解析之前调用**
 * （守卫①）；非法布局直接 throw（配置校验期已拦大部分，这里兜底运行时）。
 */
export function decodeStageGrid(
  raw: string | StageJson,
  stageId: number,
  seed?: number,
): StageData {
  let json: StageJson
  if (typeof raw === 'string') {
    try {
      json = JSON.parse(raw) as StageJson
    } catch (e) {
      throw new Error(`decodeStageGrid: stageJson 不是合法 JSON（stage ${stageId}）: ${e}`)
    }
  } else json = raw

  const grid = json.grid
  if (
    !Array.isArray(grid) ||
    grid.length !== 13 ||
    grid.some((r) => !Array.isArray(r) || r.length !== 13)
  ) {
    throw new Error(
      `decodeStageGrid: grid 必须 13×13（stage ${stageId}，收到 ${Array.isArray(grid) ? `${grid.length} 行` : '非数组'}）`,
    )
  }
  for (const row of grid) {
    for (const code of row) {
      if (!Number.isInteger(code) || code < 0 || code > 20) {
        throw new Error(`decodeStageGrid: grid 含非法瓦码 ${code}（0..20，stage ${stageId}）`)
      }
    }
  }

  const tiles = decodeLevel(grid.map((r) => r.map(Number)))
  const forces = (json.forces ?? '').slice(0, 20)
  const enemies: TankKind[] = decodeForceString(forces)
  // 守卫③：enemyCount 恒显式（tel.enemyTotal 取 stage.enemyCount ?? 20）
  const enemyCount = json.count ?? Math.max(1, forces.length || 20)

  // 出生点解析：spawn_variants 池 + seed → 确定性选点；无池/无 seed → 顶层字段
  //（向后兼容：tests 与旧课程不带 spawn_variants，行为逐字节不变）。
  let playerSpawn = json.player_spawn
  let spawnList = json.enemy_spawns ?? []
  const variants = json.spawn_variants
  if (variants && variants.length > 0) {
    const v = variants[seed === undefined ? 0 : pickVariantIndex(seed, variants.length)]
    if (v.player_spawn) playerSpawn = v.player_spawn
    if (v.enemy_spawns) spawnList = v.enemy_spawns
  }
  if (playerSpawn) assertSpawnClear(tiles, playerSpawn.col, playerSpawn.row, 'player')
  const enemySpawns = spawnList.map((s) => ({ col: s.col, row: s.row }))
  for (const s of enemySpawns) assertSpawnClear(tiles, s.col, s.row, 'enemy')

  const stage: StageData = {
    id: stageId,
    name: json.name || `custom-${stageId}`,
    tiles,
    enemies,
    enemyCount,
    ...(playerSpawn ? { playerSpawn: { col: playerSpawn.col, row: playerSpawn.row } } : {}),
    ...(enemySpawns.length ? { enemySpawns } : {}),
  }
  return stage
}
