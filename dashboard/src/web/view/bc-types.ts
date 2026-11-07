/** bc-types.ts — BC epoch / 多地图 eval 账本行（run_bc 侧入账契约）。 */
// ────────────────────────── BC epoch 指标 / 多地图 eval（2026-09-13） ──────────────────────────

/** bc_epoch 账本事件行（run_bc 从 hub bc-metrics / 本地 bc.py 流解析入账）。 */
export interface BcEpochRow {
  it: number
  epoch: number
  trainLoss: number | null
  valLoss: number | null
  moveAcc: number | null
  fireAcc: number | null
  /** v6 第三头（#11）；legacy 账本行没有该键 ⇒ null。 */
  holdAcc: number | null
  lr: number | null
  /** 事件入账时间（epoch 秒）。 */
  ts: number
}

/** 单张地图的 eval 聚合（RL 门指标口径 GATE_PLATEAU_METRICS 同词表）。 */
export interface BcEvalLevelRow {
  level: string
  n: number
  wins: number
  winRate: number
  killsMean: number
  phitsMean: number
  pickupMean: number
  timeoutFrac: number
  scoreMean: number
  meanTicks: number
  failed: number
  /** 行为门口径（B5，v6 之后的 eval 行才有）：拾取率 / 每局生成 / 探索格 / 移动占比。 */
  pickupRate?: number | null
  pickupSpawnMean?: number | null
  cellsMean?: number | null
  moveFrac?: number | null
  /** 面板口径（B4）：max_ticks / difficulty / lives / games / seed0——禁止跨面板相减。 */
  panel?: {
    level?: string
    maxTicks?: number | null
    courseMaxTicks?: number | null
    difficulty?: string
    lives?: number | null
    gamesPerStage?: number | null
    seed0?: number | null
    stages?: number | null
  }
}

/** bc_eval 账本事件行（run_bc 在 eval.every_epochs 边界派发多地图评估后写入）。 */
export interface BcEvalRow {
  it: number
  epoch: number
  wver: string
  levels: BcEvalLevelRow[]
  /** B5：周期 eval 只作哨兵，判决只认 200 局行为门。 */
  sentinelOnly?: boolean
  ts: number
}
