/** eval-types.ts — EvalBoard 视图类型（阶梯 / 批次 / 告警 / 看板 / 评估页载荷）。 */
// ────────────────────────── EvalBoard 视图类型（plan/rl-eval-system.md §8；实现 → console/evalboard.ts） ──────────────────────────

export interface EvalGateView {
  main: boolean
  cost: boolean
  style: boolean
  credible: boolean
  pass: boolean
}

export interface EvalStageBrief {
  /** stage.name，如 arena-13x13-4enemies。 */
  name: string
  /** 敌人 kind 队列（可重复），用于 hover 敌型构成。 */
  enemies: string[]
  enemyCount: number
  /** tiles 含 'E'（基地）。 */
  hasBase: boolean
  /** tiles 含 b/s/w/f/i 任一（砖/钢/水/树/冰）。 */
  hasTerrain: boolean
}

export interface EvalLadderRow {
  rung: string
  dimension: string
  lives: number
  /** DIFFICULTIES[difficulty].playerStartLevel（hard ⇒ 1）—— hover 「N★」来源。 */
  starLevel: number
  /** 该 rung 关卡画像（R1 hover 人类可读描述的数据源）。 */
  stageBrief: EvalStageBrief
  god: { winRate: number | null; lifePrice: number | null; n: number; provisional: boolean | null }
  batches: number
  n: number
  latestWin: number | null
  windowWin: number | null
  /**
   * A 层趋势（§2.1：每天训练内 eval 自动入账，**只做健康/趋势，不判能力**）。
   * = 该 rung 最近一次 iter 的读数（A 层固定 EVAL_SEEDS，逐 iter 重复采样，
   * 故只取最新 iter，绝不跨 iter 叠加 —— 叠加会重复计同一 seed）。
   */
  aTrend: { n: number; winRate: number; iter: number } | null
  deltaVsGod: number | null
  gate: EvalGateView | null
  partial: boolean
}

export interface EvalBatchRow {
  batch_id: string
  course: string
  rung_from: string
  status: string
  iter: number
  trigger: string
  units: { of: number; done: number[] }
  elapsed_sec: number | null
  created_ts: string
  /** B/C 执行语义：god = C 层基线（权重无关）；缺省按 nn。 */
  policy?: 'nn' | 'god'
  /** 权重路径；`god` 哨兵 = C 层基线（兼容未写 policy 的旧台账行）。 */
  ckpt?: string
}

export interface EvalAlert {
  id: string
  severity: 'red' | 'yellow' | 'note' | 'star'
  message: string
  blocksVerdict?: boolean
  rejectBatch?: boolean
}

export interface EvalBoardView {
  cachedAt: number
  course: string
  ingested: number
  evalEvery: number | null
  abWarn: string | null
  ladder: EvalLadderRow[]
  alerts: EvalAlert[]
  batches: EvalBatchRow[]
  flips: Array<{
    rung: string
    from: string
    to: string
    delta: number
    paired: boolean
    flips: number | null
    rate: number | null
  }>
  rows: number
  spaceCalibrated: boolean
  // ── plan/evalboard-console-ux.md §5.1 扩展（R5 iter 矩阵 / R4-G1 运行态 / R4 爬梯态） ──
  /** 行 = God + 各 B 层 iter（按 course），cells key = `${rung}.${metric}`。 */
  iterRows: EvalIterRow[]
  /** 训练侧心跳（runner_state.json，缺失 ⇒ null）。 */
  runnerState: EvalRunnerState | null
  /** 自动爬梯任务态（无任务 ⇒ null）。 */
  ladderState: EvalLadderState | null
}

/** B 层 iter 矩阵一行（R5）：iter 的累积 B 层读数。 */
export interface EvalIterRow {
  /** 'god' = C 层基线行（只填胜率类列）；'iter' = B 层学生行。 */
  kind: 'god' | 'iter'
  course: string
  iter: number
  /** key = `${rung}.${metric}`（见 EvalMetricKey）；无数据 ⇒ null。 */
  cells: Record<string, number | null>
  /** 该 iter 的 B 层累积局数。 */
  n: number
  /** 溯源（导出用）。 */
  batchIds: string[]
  /** A4：单批判阈值判定属筛查级，不作 verdict。 */
  screening: boolean
}

/** R2 可选指标 key（UI 显隐 + CSV 列名共用）。 */
export type EvalMetricKey =
  | 'winRate'
  | 'clearRate'
  | 'killCompletion'
  | 'meanKills'
  | 'meanPowerUps'
  | 'winTickMean'
  | 'winHpLeftMean'

export const EVAL_METRIC_KEYS: readonly EvalMetricKey[] = [
  'winRate',
  'clearRate',
  'killCompletion',
  'meanKills',
  'meanPowerUps',
  'winTickMean',
  'winHpLeftMean',
]

/** 指标中文短名（列头/CSV）。 */
export const EVAL_METRIC_LABELS: Record<EvalMetricKey, string> = {
  winRate: '胜率',
  clearRate: '全歼率',
  killCompletion: '完成度',
  meanKills: '平均击杀',
  meanPowerUps: '平均道具',
  winTickMean: '胜局耗时',
  winHpLeftMean: '胜局残血',
}

/** /eval 独立页（R8）SSR 载荷 + 选项。 */
export interface EvalPageOptions {
  /** 已选课程（URL ?courses=a,b）。 */
  courses: string[]
  /** 全量可选课程（discoverCourses）。 */
  allCourses: string[]
  readOnly: boolean
}

export interface EvalPagePayload {
  views: EvalBoardView[]
  options: EvalPageOptions
}

/** R7 ckpt 发现：单个权重文件元数据（**不读内容**，不算 ckpt_sha16）。 */
export interface EvalCkptFile {
  leg: string
  /** 仓库相对路径（/ 分隔）。 */
  path: string
  mtime: number
  sizeBytes: number
  /** 文件名 `*.it<N>.*.json` → N；无匹配 ⇒ null。 */
  iter: number | null
}

/** R7 发现端点返回（GET /api/evalCkpts）。 */
export interface EvalCkptsView {
  course: string
  /** 可展开的腿（目录名 + json 数）；不返回文件明细（懒加载）。 */
  legs: Array<{ leg: string; count: number }>
  /** 目标腿（显式 leg / 课程同名腿 / 活动权重）的文件明细。 */
  files: EvalCkptFile[]
  truncated: boolean
}

/** R4-G1 训练侧心跳（runner_state.json）。 */
export interface EvalRunnerState {
  windowOpen: boolean
  updatedTs: number
  batchId: string | null
  unitIdx: number | null
  unitOf: number | null
  rung: string | null
  remainingUnits: number
  lastWindowClosedTs: number | null
  engineEpoch: string
}

/** R4 自动爬梯任务态（请求文件持久化，无状态推导）。 */
export interface EvalLadderState {
  course: string
  iter: number
  threshold: number
  reachedRung: string | null
  stopped: boolean
  stoppedReason: string | null
}

/**
 * R1：rung 表头 hover 人类可读描述（纯函数，可单测）。
 * 例：`s1l3b1 · 20敌(18basic+2fast) · 3命 · 1★ · 有地形 · 有基地(可摧毁)｜基地防守`
 */
