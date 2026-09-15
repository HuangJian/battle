/** metric-types.ts — 训练指标类型（iters 数据层契约）：轮次 / eval 汇总 / 逐局 / 配对裁判。 */

// ────────────────────────── 训练指标类型（iters.ts 数据层的契约） ──────────────────────────

export interface IterActuals {
  games: number
  totalKills: number
  totalPU: number
  avgTicks: number
  /** 胜局平均残血（hp 单位：剩余命每命计满额 maxHp + 当前 hp）。
   *  null = 数据源无 residualHp（旧 manifest / 无胜局）。 */
  avgResidualHp: number | null
  /** 胜局平均耗时（ticks，仅胜局计入）；null = 无胜局或胜局缺 ticks。
   *  rollout 实际值口径（所有 iter 采样）；旧缓存/缺字段 = undefined。 */
  avgWinTicks?: number | null
  /** 败局平均耗时（ticks，仅败局计入）；null = 无败局或缺数据。
   *  ⚠️ 方向警告同 EvalSummary.avgLossTicks：越高不代表越强。 */
  avgLossTicks?: number | null
  /** 每杀承伤（ΣplayerDamageTaken / Σkills，全样本口径）；null = 该轮无击杀。 */
  dmgPerKill?: number | null
}

export interface EvalSummary {
  time: string
  games: number
  wins: number
  winRate: number | null
  clears: number
  clearRate: number | null
  dropped: number
  sec: number
  wver: string
  outcomes: Record<string, number>
  avgTicks: number | null
  /** 胜局平均耗时（ticks，仅胜局计入）；null = 无胜局或胜局缺 ticks。 */
  avgWinTicks: number | null
  totalKills: number | null
  totalPU: number | null
  /** 胜局平均残血；null = 评估记录无 residualHp 字段。 */
  avgResidualHp: number | null
  /** 败局平均耗时（ticks，仅败局计入）；null = 无败局或败局缺 ticks。
   *  ⚠️ **方向警告**：越高**不代表**越强——在同构关里"活得久"往往是**打不死敌人**的症状
   *  （清场停滞）。2026-09-11 实测：c4 上它与胜率**负相关**（vs 迭代 Spearman +0.964，
   *  而胜率为 −0.893，二者符号相反）。必须与胜率并排解读。 */
  avgLossTicks: number | null
  /** 每杀承伤（ΣplayerDamageTaken / Σkills，**全样本**口径）：越小越会"周旋"。
   *  自动防苟活——不打 ⇒ 分母小 ⇒ 值爆炸；自动防无脑冲——掉血多 ⇒ 值大。
   *  null = 该轮无击杀。 */
  dmgPerKill: number | null
  scoreMean: number | null
  scoreStd: number | null
  /** 锚点轨胜率（双轨日常评估；非双轨 / 无锚点局 → null）。 */
  anchorWr?: number | null
  /** 轮转轨胜率（双轨日常评估；it0 基线 / 无轮转局 → null）。 */
  rotorWr?: number | null
  /** 过拟合 gap = 锚点 − 轮转（百分点）；任一轨缺失 → null。 */
  overfitGapPp?: number | null
  /** 本轮 vs 开腿首轮的配对比较（同语料逐 seed；基线轮/无数据为 null）。 */
  pairedVsFirst?: PairedCompare | null
}

export interface IterRow {
  iter: number
  time: string
  winRate: number
  scoreMean: number
  scoreStd: number
  samples: number
  /** 采集窗口墙钟（collector 退出锚点；流式含与 PPO 重叠的墙钟）。 */
  rolloutSec: number
  /** 更新墙钟：本机/流式 = 纯训练；远端 = 往返（含打包/上传/排队/下载）。 */
  ppoSec: number
  /** 纯采集秒（末局结算 − 权重分发完毕）；旧账本无此键 → null（回退 rolloutSec）。 */
  pureCollectSec: number | null
  /** 云端/本机真训练秒；旧账本无此键 → null（回退 ppoSec）。 */
  ppoCloudSec: number | null
  /** 权重下发阶段（ping+POST）秒；旧账本无此键 → null（按 0 计）。 */
  distPhaseSec: number | null
  kl: number
  entropy: number
  policyLoss: number
  valueLoss: number
  meanRet: number
  lr: number
  expectedGames: number
  halted: boolean
  topDims: string
  avgTicks: number
  accuracy: number
  loot: number
  kills: number
  actuals: IterActuals | null
  evalData: EvalSummary | null
}

/** eval 逐局类型（导出 replay 弹窗「类型」列）：win→胜利；outcome=max_ticks→超时；其余→失败。 */
export type EvalGameClass = 'win' | 'fail' | 'timeout'

export const EVAL_GAME_CLASS_LABEL: Record<EvalGameClass, string> = {
  win: '胜利',
  fail: '失败',
  timeout: '超时',
}

/** 单局 eval 行（最新 in-loop eval 逐局视图；iters.readLatestEvalGames 产出）。 */
export interface EvalGameRow {
  stage: number
  seed: number
  /** 人类可读关名（真实关 = STAGES.name；arena = 阶名；自定义 = 自定义关 <id>）。 */
  stageName: string
  cls: EvalGameClass
  /** 全歼（win 的超时截断局可能 cleared——口径差异诚实披露）。 */
  cleared: boolean
  outcome: string
  ticks: number
  kills: number
  /** 非致命扣血累计（null = 账本行无该字段——老课程）。 */
  dmgTaken: number | null
  /** 承伤/杀（kills>0 且 dmgTaken 非空才有定义）。 */
  dmgPerKill: number | null
  /** 胜局残血（败局恒 null——聚合口径只计胜局）。 */
  residualHp: number | null
  pu: number
  score: number | null
  node: string
  time: string
}

/** GET /api/evalGames 载荷：最新 in-loop eval 概要 + 逐局行（导出 replay 弹窗）。 */
export interface EvalGamesData {
  iter: number
  wver: string
  time: string
  games: number
  wins: number
  winRate: number | null
  clears: number
  outcomes: Record<string, number>
  rows: EvalGameRow[]
}

export interface EvalGamesView extends EvalGamesData {
  course: string
  available: boolean
}

/** POST evalReplays 产物清单（python rl/eval_replays_once.py 落盘的 manifest JSON）。 */
export interface EvalReplayManifest {
  ok: boolean
  course: string
  iter: number
  wver: string
  weightsPath: string
  difficulty: string
  maxTicks: number
  generatedAt: string
  sec: number
  requested: number
  files: Array<{ stage: number; seed: number; file: string }>
  errors: Array<{ stage: number; seed: number; error: string }>
  /** 重放局 vs eval_log 账本逐字段对账（outcome/ticks/kills）——确定性契约的对账项。 */
  mismatches: Array<{ stage: number; seed: number; field: string; ledger: unknown; resim: unknown }>
}

/** GET /api/evalReplayJob 载荷：导出任务态（running = busy 互斥；manifest = 落盘产物）。 */
export interface EvalReplayJobView {
  course: string
  running: boolean
  manifest: EvalReplayManifest | null
  logTail: string[]
}

/** 配对裁判单组对比（同语料逐 seed 配对，见 tools/eval/mcnemar.ts）。 */
export interface PairedCompare {
  baseIter: number
  ckptIter: number
  /** 配上对的局数（只看 b01/b10 的分母）。 */
  paired: number
  /** 一边缺席而丢弃的局数（失败/重试口径差异所致，只诚实披露不参与判定）。 */
  unpaired: number
  /** 基线输、新权重赢（政绩）。 */
  b01: number
  /** 基线赢、新权重输（学费）。 */
  b10: number
  /** 净涨幅百分点（1 位小数）。 */
  deltaPp: number
  /** McNemar 精确二项双侧 p 值。 */
  p: number
  verdict: 'up' | 'down' | 'flat'
}

/** 配对裁判（只读哨子，不进门判）：最新 eval vs 开腿首轮 / vs 上一 eval 轮。 */
export interface PairedReferee {
  vsFirst: PairedCompare | null
  vsPrev: PairedCompare | null
}
