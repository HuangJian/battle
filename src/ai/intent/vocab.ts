/**
 * intent/vocab.ts — Intent-Policy NN 词表契约（plan/Intent-Policy-NN-Plan.md §3，M0a 定稿）。
 *
 * 本文件是 8 类意图词表的**唯一实现**：正向映射（细分支→候选→意图）、反向白名单
 * （意图→候选，三层标注）、激活头矩阵、目标敌槽序函数、分段规则四件套常量、
 * ENDGAME 切分谓词、死类掩码约定。God-AI tagger（M1）、机械探针 tagger（M0b/§3.6）、
 * 执行器（M6）与签名标签器（M2）一律 import 此模块——禁止任何第二份映射表。
 *
 * ── 预注册值（写死，改动 = 计划修订并回写 DECISIONS）──
 *   分段去抖 N            = 4 tick（建议带 3–5 的中位；hard/chaos thinkInterval=2 下
 *                           ≈2 个决策更新，滤单决策抖动而不吞拾取窗）
 *   reflex 透明集合        = {dodge, survive}（P0-2②；其帧标签 = 当前活跃意图）
 *   短段处置              = 归前段（无前段归后段；整局皆短段则弃帧）
 *   战斗链判定顺序         = 回防(基地受威胁∧玩家距基地>maxPlayerDistFromBase)
 *                           > CRUISE(enemiesRemaining≤endgameEnemyThreshold) > HUNT
 *   目标敌槽序（预注册#25） = 距基地 Manhattan 升序，tie-break 行主扫描序（行优先、列次之）
 *   死类裁决下限           = 自然分布窗口计数 <200 → reflex-only 掩码（P0-3/#28）
 */

import { BASE_POS, CELL } from '../../constants'
import type { World } from '../../game/World'
import type { Tank } from '../../types'

/** 学习词表 8 主类。id 即数据集/日志中的稳定字符串键。 */
export const INTENT_IDS = [
  'INTERCEPT',
  'RETURN_DEFENSE',
  'HUNT',
  'HOLD_LANE',
  'CLEAR',
  'PICKUP',
  'CRUISE',
  'ESCAPE',
] as const
export type IntentId = (typeof INTENT_IDS)[number]

export const INTENT_DIM = INTENT_IDS.length // 8

/**
 * 意图语义一行表（§3.1 全量挂靠版的索引列；逐候选挂靠行见 FORWARD_MAP 注释块）：
 *   INTERCEPT      拦截进逼之敌（基地方向威胁）
 *   RETURN_DEFENSE 回防/据守锚点
 *   HUNT           主动追击
 *   HOLD_LANE      走廊/中路据守
 *   CLEAR          清障开路
 *   PICKUP         被动拾取（仅自动生效项；不因拾取打断追击语义）
 *   CRUISE         巡航与收尾（ENDGAME 参数组区间）
 *   ESCAPE         脱险自保（预期被 <200 窗口裁决降为 reflex-only）
 */

/**
 * 正向映射第①层：细分支标签（GodAIInput._lastBranch 的 label 口径，细于 19 个
 * ActionId）→ 意图。COMBAT_CHAIN 哨兵表示"由战斗链谓词在线判定"（见
 * classifyCombatIntent）——同一细分支在不同 World 状态下合法地落入不同意图，
 * 这是 INTERCEPT vs HUNT 的预注册区分判据（敌是否威胁基地/玩家是否在回防距离外），
 * 禁止人眼临场判。
 *
 * 19 候选全量挂靠（候选 → 细分支 → 意图；OFF = 默认链不可达，仍须有归属行）：
 *   suicideReturn(OFF)     'suicideReturn'      RETURN_DEFENSE（携带压制 dodge 标记）
 *   dodge                  'dodge'              REFLEX 透明（不打断分段）
 *   interceptBase          't8'                 INTERCEPT
 *   unifiedCandidates(OFF) 'candidateIntercept' INTERCEPT
 *                          'candidateKill'      战斗链
 *                          'candidateReturn'    RETURN_DEFENSE
 *                          'candidateClear'     CLEAR
 *   baseLaneSentry         'baseLaneSentry'     INTERCEPT（§198 卫位导航）
 *   pickupHigh/Mid/Low     'powerup'            PICKUP
 *   aggro                  'aggressive'(战斗链) / 'powerup'(PICKUP)
 *   defenseIntercept       'defenseIntercept'   INTERCEPT（防守位停射拦截）
 *   midLaneDefense         'midLaneDefense'     HOLD_LANE（§163/§195 钻探粘性口径随执行器）
 *   closePickup            'powerup'            PICKUP
 *   engage                 't2a'                战斗链
 *   pickupLow              'powerup'            PICKUP
 *   firingLane(OFF)        'firingLane'         战斗链（火力死区解除=重新接战）
 *   baseConnectClear       'baseConnectClear'   CLEAR（§189 开局联通）
 *   carvePath(OFF)         'carvePath'          CLEAR（§161 开路）
 *   midLaneHold(OFF)       'midLaneHold'        HOLD_LANE
 *   hunt                   'navigate'           战斗链
 *   survive(OFF)           'survive'            REFLEX 透明
 *
 * 外壳态（非意图，不进映射）：'dead'=跳帧；'hold'=继承上一决策的意图（thinkInterval
 * 保持期即当前意图的持续——replan cadence 语义的一部分）。
 */
export const SHELL_LABELS = ['dead', 'hold'] as const

export const REFLEX_TRANSPARENT_LABELS = ['dodge', 'survive'] as const

export const COMBAT_CHAIN = 'COMBAT_CHAIN' as const

const STATIC_FORWARD: Record<string, Exclude<IntentId, 'CRUISE'> | typeof COMBAT_CHAIN> = {
  t8: 'INTERCEPT',
  baseLaneSentry: 'INTERCEPT',
  defenseIntercept: 'INTERCEPT',
  candidateIntercept: 'INTERCEPT',
  candidateReturn: 'RETURN_DEFENSE',
  suicideReturn: 'RETURN_DEFENSE',
  candidateClear: 'CLEAR',
  carvePath: 'CLEAR',
  baseConnectClear: 'CLEAR',
  powerup: 'PICKUP',
  midLaneDefense: 'HOLD_LANE',
  midLaneHold: 'HOLD_LANE',
  t2a: COMBAT_CHAIN,
  navigate: COMBAT_CHAIN,
  aggressive: COMBAT_CHAIN,
  firingLane: COMBAT_CHAIN,
  candidateKill: COMBAT_CHAIN,
}

export function forwardMapLabel(
  label: string,
):
  | { kind: 'shell'; label: string }
  | { kind: 'reflex'; label: string }
  | { kind: 'static'; intent: IntentId }
  | { kind: 'combat-chain'; label: string } {
  if ((SHELL_LABELS as readonly string[]).includes(label)) return { kind: 'shell', label }
  if ((REFLEX_TRANSPARENT_LABELS as readonly string[]).includes(label))
    return { kind: 'reflex', label }
  const mapped = STATIC_FORWARD[label]
  if (!mapped) throw new Error(`intent/vocab: unmapped fine-branch label "${label}"`)
  if (mapped === COMBAT_CHAIN) return { kind: 'combat-chain', label }
  return { kind: 'static', intent: mapped }
}

/** 反向完备性断言的数据面：每个非 reflex 细分支必须出现在 ≥1 个意图白名单内。 */
export const ALL_NON_REFLEX_LABELS: readonly string[] = Object.keys(STATIC_FORWARD)

/**
 * 反向映射：意图 → 候选白名单（P0-1，按能力需要定义而非正向镜像）。
 * 三层标注（P0-5 执行器三层契约）：
 *   window  — window-locked 意图主行为（承诺期内分派器只在此层内选择）
 *   overlay — per-tick 跨意图辅助（顺路拾取等；仲裁规则：PICKUP overlay 只在
 *             无直接威胁时生效）
 *   reflex  — 逐 tick 硬代码闪避；覆盖移动默认成立，例外只能经显式压制标记
 *             （suppressDodge：目前仅 suicideReturn 一处，§116/§117 默认 OFF——
 *             它是"意图→reflex 调制通道必须规格化"的现成反例）
 */
export type ExecLayer = 'window' | 'overlay' | 'reflex'

export interface WhitelistEntry {
  /** 细分支标签（粒度与正向映射第①层一致）。 */
  branch: string
  layer: ExecLayer
  /** 显式压制标记：该 window 分派生效时压制 reflex dodge。 */
  suppressDodge?: boolean
}

export const WHITELISTS: Record<IntentId, readonly WhitelistEntry[]> = {
  INTERCEPT: [
    { branch: 't8', layer: 'window' },
    { branch: 'baseLaneSentry', layer: 'window' },
    { branch: 'defenseIntercept', layer: 'window' },
    { branch: 'candidateIntercept', layer: 'window' },
    { branch: 't2a', layer: 'window' },
    { branch: 'aggressive', layer: 'window' },
    { branch: 'powerup', layer: 'overlay' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  RETURN_DEFENSE: [
    { branch: 'suicideReturn', layer: 'window', suppressDodge: true },
    { branch: 'candidateReturn', layer: 'window' },
    { branch: 'navigate', layer: 'window' },
    { branch: 'powerup', layer: 'overlay' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  HUNT: [
    { branch: 'navigate', layer: 'window' },
    { branch: 't2a', layer: 'window' },
    { branch: 'aggressive', layer: 'window' },
    { branch: 'firingLane', layer: 'window' },
    { branch: 'candidateKill', layer: 'window' },
    { branch: 'powerup', layer: 'overlay' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  HOLD_LANE: [
    { branch: 'midLaneDefense', layer: 'window' },
    { branch: 'midLaneHold', layer: 'window' },
    { branch: 'powerup', layer: 'overlay' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  CLEAR: [
    { branch: 'carvePath', layer: 'window' },
    { branch: 'baseConnectClear', layer: 'window' },
    { branch: 'candidateClear', layer: 'window' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  PICKUP: [
    { branch: 'powerup', layer: 'window' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  CRUISE: [
    { branch: 'navigate', layer: 'window' },
    { branch: 't2a', layer: 'window' },
    { branch: 'aggressive', layer: 'window' },
    { branch: 'powerup', layer: 'overlay' },
    { branch: 'dodge', layer: 'reflex' },
  ],
  ESCAPE: [
    { branch: 'survive', layer: 'window' },
    { branch: 'dodge', layer: 'reflex' },
  ],
}

/**
 * 父意图→激活头矩阵（§3.5 / 预注册 #9）。非激活头：训练损失权重置 0（不产梯度、
 * 不计 acc、不喂占位标签）；推理时仅当意图头选中该类后才解释其激活头；执行器
 * 契约写明"未激活头输出必须忽略"。草案定稿：enemy 头 ⊆ {INTERCEPT,HUNT,CRUISE}；
 * anchor 头 ⊆ {RETURN_DEFENSE,HOLD_LANE,CRUISE}；PICKUP/CLEAR/ESCAPE 不解释任何头
 * （目标由执行器规则决定）；CRUISE 双激活（追残敌为主、锚点巡逻为辅，执行器按
 * 敌头 argmax≠none 优先解释敌头）。
 */
export interface HeadActivation {
  enemy: 0 | 1
  anchor: 0 | 1
}

export const ACTIVATION_MATRIX: Record<IntentId, HeadActivation> = {
  INTERCEPT: { enemy: 1, anchor: 0 },
  RETURN_DEFENSE: { enemy: 0, anchor: 1 },
  HUNT: { enemy: 1, anchor: 0 },
  HOLD_LANE: { enemy: 0, anchor: 1 },
  CLEAR: { enemy: 0, anchor: 0 },
  PICKUP: { enemy: 0, anchor: 0 },
  CRUISE: { enemy: 1, anchor: 1 },
  ESCAPE: { enemy: 0, anchor: 0 },
}

/** 目标敌头维度：none + e0..e3（槽序固定，MAX_ENEMIES_ALIVE=4）。 */
export const ENEMY_HEAD_DIM = 5
export const NONE_ENEMY_SLOT = 0

/**
 * 目标敌槽序函数（预注册 #25）：obs 可计算的确定性空间函数——距基地 Manhattan
 * 升序，tie-break 行主扫描序（行小者先，再列小者先）。tagger 与执行器共享本实现
 * （禁两份）。仅在 replan/打标时刻调用，允许返回新数组（非热路径）。
 *
 * 输入过滤：alive ∧ ¬isPlayer ∧ spawnTimer≤0（与 God-AI 敌集合口径一致）；
 * 多于 4 个时截断（MAX_ENEMIES_ALIVE=4 上界内不会发生，防御性截断保槽序稳定）。
 */
export function enemySlotOrder(world: World): Tank[] {
  const out: Tank[] = []
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && !t.isPlayer && t.spawnTimer <= 0) out.push(t)
  }
  const rank = (t: Tank): number => {
    const c = cellOf(t.x, t.y)
    return (
      (Math.abs(c.col - BASE_POS.col) + Math.abs(c.row - BASE_POS.row)) * 1024 + c.row * 32 + c.col
    )
  }
  // 单一数值秩：dist×1024 + row×32 + col —— 行主扫描序 tie-break 无比较器分配。
  return out.sort((a, b) => rank(a) - rank(b)).slice(0, 4)
}

/** 敌 tank id → 槽位（1..4）；不在前 4 槽返回 0（none）。 */
export function enemySlotOf(world: World, tankId: number): number {
  const slots = enemySlotOrder(world)
  for (let i = 0; i < slots.length; i++) if (slots[i].id === tankId) return i + 1
  return NONE_ENEMY_SLOT
}

/**
 * CRUISE/ENDGAME 切分谓词（P2k3-4）：复用 God-AI 现役 ENDGAME 门（StrategyPlanner
 * 同式）——出生队列剩余敌人 ≤ endgameEnemyThreshold（hard 默认 6）。纯 World 读。
 */
export function isEndgameRegime(world: World, endgameEnemyThreshold: number): boolean {
  return world.enemiesRemaining <= endgameEnemyThreshold
}

/** 战斗链谓词输入原语（与 GodAIInput 解耦，便于 headless 测试与机械 tagger 复用）。 */
export interface CombatChainInput {
  isBaseUnderThreat: boolean
  /** 玩家格到基地的 Manhattan 距离（cell）。 */
  playerDistToBase: number
  maxPlayerDistFromBase: number
  isEndgame: boolean
}

/**
 * 战斗链判定（预注册顺序，禁止临场改序）：
 *   1. 基地受威胁 ∧ 玩家在回防距离外（>maxPlayerDistFromBase，§159 同款阈值）→
 *      RETURN_DEFENSE（回防赶路段）
 *   2. ENDGAME 区间 → CRUISE
 *   3. 其余 → HUNT
 * 基地受威胁但玩家已近基地时不在此判——那类帧由防守族候选（INTERCEPT/HOLD_LANE
 * 静态行）承接，战斗链只在战斗族分支提交时被问询。
 */
export function classifyCombatIntent(inp: CombatChainInput): 'RETURN_DEFENSE' | 'CRUISE' | 'HUNT' {
  if (inp.isBaseUnderThreat && inp.playerDistToBase > inp.maxPlayerDistFromBase)
    return 'RETURN_DEFENSE'
  if (inp.isEndgame) return 'CRUISE'
  return 'HUNT'
}

// ─── 分段规则四件套（P0-2 / 预注册 #19）────────────────────────────────────

/** ①去抖：候选连续提交 ≥N tick 才构成稳定意图段。 */
export const SEGMENT_DEBOUNCE_N = 4

/**
 * ③短段处置：去抖后仍 <N tick 的孤立段并入前段（标签沿用前段）；局首短段并入
 * 后段；整局皆短段则该局不产出帧。边界帧采样（§5.1 转移包含式）在分段结果上取。
 */

/**
 * ④探针同步：机械 tagger（M0b/§3.6）必须 import 本模块实现同一分段规则（含 N），
 * 由 tests/intent-vocab.test.ts 的共享断言锁定——探针测的才是最终标签函数的可学习性。
 */

/**
 * 逐 tick 打标流 → 稳定意图段序列（唯一实现，probe/M1 tagger 共用）。
 *
 * 输入帧形：{ label: string; combat: CombatChainInput | null }。combat 仅当该帧
 * 细分支为战斗链哨兵时提供（机械 tagger 从 World 现算；M1 tagger 从挂靠点现算）。
 *
 * 四件套实现顺序：
 *   A. 逐帧解析为意图 | null（shell 'dead'/'hold' 与 reflex 'dodge'/'survive' →
 *      null 透传帧；未带 combat 原语的战斗链帧同样透传）；
 *   B. ②reflex 透明：null 帧向前继承活跃意图，局首 null 向后继承首个意图；
 *   C. 游程编码后 ①去抖（≥N 成段）+ ③短段处置：非局首短段并入前段（标签沿用
 *      前段），局首短段并入后段；相邻同意图段合并。
 * 返回闭区间段列表 [{start,end,intent}]。
 */
export interface TagFrame {
  label: string
  combat: CombatChainInput | null
}

export interface IntentSegment {
  start: number
  end: number
  intent: IntentId
}

interface RawRun {
  start: number
  end: number
  intent: IntentId
}

export function segmentIntents(frames: readonly TagFrame[]): IntentSegment[] {
  return segmentIntentSeq(
    frames.map((f): IntentId | null => {
      const m = forwardMapLabel(f.label)
      if (m.kind === 'static') return m.intent
      if (m.kind === 'combat-chain' && f.combat) return classifyCombatIntent(f.combat)
      return null
    }),
  )
}

/**
 * 分段四件套底核：输入逐帧意图序列（IntentId | null，null=透传帧），输出稳定段。
 * God-AI 标签流（segmentIntents 经 forwardMap）与人像签名流（segmentIntentSeq 直喂）
 * 共用同一实现（M2 签名器 ↔ M1 tagger 分段同步）。
 */
export function segmentIntentSeq(seq: readonly (IntentId | null)[]): IntentSegment[] {
  const n = seq.length
  if (n === 0) return []

  // A. 解析（上游已提供意图或 null）。
  const resolved = [...seq]

  // B. reflex/shell 透明：向前继承；局首向后继承。
  let firstIdx = resolved.findIndex((v) => v !== null)
  if (firstIdx < 0) return []
  for (let i = 0; i < firstIdx; i++) resolved[i] = resolved[firstIdx]
  for (let i = firstIdx + 1; i < n; i++) if (resolved[i] === null) resolved[i] = resolved[i - 1]

  // C. 游程编码 + 去抖/短段归并（单趟：短段贴前段；局首短段落入下一轮与后段合并）。
  const runs: RawRun[] = []
  for (let i = 0; i < n;) {
    const intent = resolved[i] as IntentId
    let j = i + 1
    while (j < n && resolved[j] === intent) j++
    const run: RawRun = { start: i, end: j - 1, intent }
    const last = runs[runs.length - 1]
    if (last && j - i < SEGMENT_DEBOUNCE_N) {
      last.end = run.end // ③：非局首短段归前段
    } else {
      runs.push(run)
    }
    i = j
  }
  // 局首短段归后段（③）。
  if (runs.length > 1 && runs[0].end - runs[0].start + 1 < SEGMENT_DEBOUNCE_N) {
    runs[1].start = runs[0].start
    runs.shift()
  }
  // 相邻同意图合并（短段归并可能制造相邻同标签）。
  const merged: RawRun[] = []
  for (const r of runs) {
    const last = merged[merged.length - 1]
    if (last && last.intent === r.intent) last.end = r.end
    else merged.push(r)
  }
  return merged
}

/** 段序列展开回逐帧标签（转移包含式采样的底座）。 */
export function expandSegments(
  segments: readonly IntentSegment[],
  frameCount: number,
): (IntentId | null)[] {
  const out: (IntentId | null)[] = new Array(frameCount).fill(null)
  for (const s of segments) for (let t = s.start; t <= s.end; t++) out[t] = s.intent
  return out
}

// ─── 死类掩码约定（P0-3 / P2k3-5 / 预注册 #28）────────────────────────────

/** 可学性裁决下限：自然分布窗口绝对计数 <200 的类降为 reflex-only。 */
export const MIN_WINDOWS_PER_CLASS = 200

/**
 * 幽灵表（自然分布窗口计数）→ 学习掩码（true = 参与训练/解释）。
 * 裁决用【自然分布】口径（全 2100 局逐 tick 流的稳定段计数），非训练构成口径。
 * 被掩码类的职能交硬代码 reflex/执行器规则，学习词表相应收缩（头维度不变，
 * logit 被 mask——避免 schema bump）。
 */
export function survivalMask(windowCounts: Record<IntentId, number>): Record<IntentId, boolean> {
  const mask = {} as Record<IntentId, boolean>
  for (const id of INTENT_IDS) mask[id] = windowCounts[id] >= MIN_WINDOWS_PER_CLASS
  return mask
}

// ─── 锚点角色注册表（I3 / Q4，16 实例级槽位）─────────────────────────────────

/**
 * 锚点 role = 实例级槽位：每 role 在本关解析出**唯一坐标**（关卡加载时校验）。
 * 解析器统一注册表聚合现有实现（computeBaseGuardAnchorsImpl / findLaneDefense-
 * PointImpl 等），无现成实现的 role 用确定性几何回退（就近可用格，写死于此），
 * 回退规则镜像进 tagger（标签-执行一致）。解析报表工具：tools/intent/anchor-report.ts。
 */
export const ANCHOR_ROLE_IDS = [
  'BASE_GUARD_0',
  'BASE_GUARD_1',
  'BASE_GUARD_2',
  'BASE_GUARD_3',
  'LANE_HOLD_0',
  'LANE_HOLD_1',
  'LANE_HOLD_2',
  'MID_FLANK_0',
  'MID_FLANK_1',
  'SPAWN_WATCH_0',
  'SPAWN_WATCH_1',
  'RETREAT_0',
  'RETREAT_1',
  'RESERVE_0',
  'RESERVE_1',
  'RESERVE_2',
] as const
export type AnchorRoleId = (typeof ANCHOR_ROLE_IDS)[number]
export const ANCHOR_HEAD_DIM = ANCHOR_ROLE_IDS.length // 16

/** 注入特征维（§4.2）：当前意图 one-hot(8) + 意图持续时长(1)，拼接于 FC 后。 */
export const INJECTION_DIM = INTENT_DIM + 1 // 9
/** 时长归一化（预注册 #11）：min(duration/D_max, 1)，D_max 初值 300 tick。 */
export const DURATION_MAX_TICKS = 300

/** 三头网络输出维度汇总（网络构建 M4 直接消费）。 */
export const INTENT_NET_HEADS = {
  intent: INTENT_DIM, // 8
  enemy: ENEMY_HEAD_DIM, // 5
  anchor: ANCHOR_HEAD_DIM, // 16
} as const

/** px 坐标 → 格坐标（与 utils/helpers 对齐的本地引用，避免循环依赖）。 */
function cellOf(x: number, y: number): { col: number; row: number } {
  return { col: Math.floor(x / CELL), row: Math.floor(y / CELL) }
}
