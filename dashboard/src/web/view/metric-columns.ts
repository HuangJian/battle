/** metric-columns.ts — **逐轮指标列的唯一模型**（表头文字 / hover 口径 / 数字对齐）。
 *
 *  为什么需要它（docs/dashboard-redesign.md §6 P3c 与 DECISIONS §2026-09-20-metric-table-single-home）：
 *  同一套逐轮指标被渲染在两个地方——首页 Hero 的「最新 6 轮」速览表，与 `/metrics` 的完整表
 *  （`DataTable`，带搜索/排序/列显隐）。两个渲染器的**结构**本来就该不同（速览 vs 分析），
 *  但**列模型**不该有两份：此前两处的表头文字与 hover 口径各写一遍，能一致纯靠人手抄得仔细
 *  ——改一处漏一处只是时间问题。
 *
 *  本仓已有正确先例：配对列（b01/b10/p/delta/过拟合）的 hover 文案早就抽到 `view/format.ts`
 *  由两处共用（`PAIRED_COL_TITLES` / `OVERFIT_COL_TITLE`，注释写着「防两处分化」）。本模块把
 *  同一条做法扩到全部列。
 *
 *  三个不变量（改列时按它判断）：
 *    1. **label / title 只在这里写一次**——两个面板都不许再出现表头字面量。
 *    2. **列顺序也只在这里**（`MAIN_ROW_KEYS` / `EVAL_ROW_KEYS`）；渲染器只按顺序摆 `<td>`，
 *       不许自己决定「哪一列在前」。
 *    3. `num` 同时决定右对齐与 `tabular-nums`（数字列的两位一体，避免一处对齐一处不齐）。
 *
 *  本模块纯数据 + 纯函数（无 JSX / 无 IO），与其它 `view/` 模块同规：服务端与客户端共用。
 */

import { OVERFIT_COL_TITLE, PAIRED_COL_TITLES } from './format'

// ────────────────────────── 键 ──────────────────────────

/** 首页「最新 6 轮 → 主行」表的列键（顺序见 `MAIN_ROW_KEYS`）。 */
export type MainRowColKey =
  | 'iter'
  | 'time'
  | 'winRate'
  | 'evalCell'
  | 'avgWinTicks'
  | 'kills'
  | 'dmgPerKill'
  | 'residualHp'
  | 'loot'
  | 'phaseSecs'
  | 'scoreMean'
  | 'kl'
  | 'entropy'
  | 'meanRet'
  | 'lr'

/** 首页「最新 6 轮 → eval」表的列键（顺序见 `EVAL_ROW_KEYS`）。
 *
 *  与主行表的差异是**真的语义差异**，不是排版差异：eval 表看的是「这一轮干净评估有多可信」，
 *  所以多了配对四列（b01/b10/p/delta）与过拟合，少了 PPO 诊断列（KL/熵/mean_ret/lr——它们与
 *  eval 无关）；`evalCell`（主行那个「有没有 eval / 可点入队」的格子）换成 `evalRate`。 */
export type EvalRowColKey =
  | 'iter'
  | 'time'
  | 'evalRate'
  | 'b01'
  | 'b10'
  | 'pairedP'
  | 'delta'
  | 'overfit'
  | 'avgWinTicks'
  | 'kills'
  | 'dmgPerKill'
  | 'residualHp'
  | 'loot'
  | 'scoreMean'
  | 'evalSec'
  | 'wver'

/** 全部列键（两个表与 `MetricsTable` 的并集）。 */
export type MetricColKey = MainRowColKey | EvalRowColKey

// ────────────────────────── 模型 ──────────────────────────

export interface MetricCol<K extends MetricColKey = MetricColKey> {
  key: K
  /** 表头文字（唯一一份）。 */
  label: string
  /** 表头 hover 口径（大白话；缺省 = 这一列不必解释）。 */
  title?: string
  /** 数字列：右对齐 + `tabular-nums`（竖着比大小用）。 */
  num?: boolean
}

/** 列模型总表（键 → 定义）。**改列只改这里。** */
export const METRIC_COLS: Readonly<Record<MetricColKey, MetricCol>> = {
  // ── 定位列 ──
  iter: { key: 'iter', label: 'iter', num: true },
  time: { key: 'time', label: '时间' },

  // ── 胜率 ──
  winRate: {
    key: 'winRate',
    label: '胜率',
    title: '本轮 rollout 采样胜率（训练时边采边算，含探索；与下面的干净评估不是一回事）',
  },
  evalCell: {
    key: 'evalCell',
    label: 'eval',
    title: '该轮的干净评估；还没有评估时可点「eval A」用该轮权重跑一次（写 eval_log）',
  },
  evalRate: {
    key: 'evalRate',
    label: 'eval 胜率',
    title: '干净评估（greedy 固定语料）胜率——与采样胜率的差距就是过拟合信号',
  },

  // ── 技能读数（rollout 实际值；缺盘上数据时按估算标注） ──
  avgWinTicks: { key: 'avgWinTicks', label: '胜局耗时', title: '胜局平均耗时（ticks）', num: true },
  kills: { key: 'kills', label: '击杀', title: '歼灭率 = Σ击杀 / Σ关卡敌数', num: true },
  dmgPerKill: {
    key: 'dmgPerKill',
    label: '承伤/杀',
    title: '每杀承伤 / (命数×满血)；越小越会周旋',
    num: true,
  },
  residualHp: {
    key: 'residualHp',
    label: '残血',
    title: '胜局残血 / 该局可支配生命容量',
    num: true,
  },
  loot: { key: 'loot', label: '道具', title: '每局平均拾取数/掉落数', num: true },

  // ── 训练诊断（rollout 行专属） ──
  phaseSecs: {
    key: 'phaseSecs',
    label: 'rollout/ppo/net',
    title: 'rollout 纯采集 / ppo 真训练 / net 网络·排队',
    num: true,
  },
  scoreMean: { key: 'scoreMean', label: '得分', title: '课程得分（越高越好）', num: true },
  kl: { key: 'kl', label: 'KL', title: '策略相对上一轮的偏移；过大会触发 halt' },
  entropy: { key: 'entropy', label: '熵', title: '策略熵；塌到 0 = 不再探索', num: true },
  meanRet: { key: 'meanRet', label: 'mean_ret', title: '本批 reward 均值（未归一化）' },
  lr: { key: 'lr', label: 'lr', title: '当前学习率（含调度）', num: true },

  // ── 配对裁判（eval 专属；文案与 `view/format.ts` 同源，两处共用一份） ──
  b01: { key: 'b01', label: 'b01', title: PAIRED_COL_TITLES.b01, num: true },
  b10: { key: 'b10', label: 'b10', title: PAIRED_COL_TITLES.b10, num: true },
  pairedP: { key: 'pairedP', label: 'p', title: PAIRED_COL_TITLES.p, num: true },
  delta: { key: 'delta', label: 'delta', title: PAIRED_COL_TITLES.delta, num: true },
  overfit: { key: 'overfit', label: '过拟合', title: OVERFIT_COL_TITLE, num: true },

  // ── eval 专属 ──
  evalSec: {
    key: 'evalSec',
    label: '用时',
    title: '评估窗口用时（该轮 eval 从开始到收齐）',
    num: true,
  },
  wver: { key: 'wver', label: 'wver', title: '评估所用权重版本（显示前 7 位）' },
}

/** 首页主行表的列顺序。**顺序只在这里**（渲染器不再自己决定先后）。 */
export const MAIN_ROW_KEYS: readonly MainRowColKey[] = [
  'iter',
  'time',
  'winRate',
  'evalCell',
  'avgWinTicks',
  'kills',
  'dmgPerKill',
  'residualHp',
  'loot',
  'phaseSecs',
  'scoreMean',
  'kl',
  'entropy',
  'meanRet',
  'lr',
]

/** 首页 eval 视图的列顺序（与 `MetricsTable` 的 `eval` 过滤模式逐列一致）。 */
export const EVAL_ROW_KEYS: readonly EvalRowColKey[] = [
  'iter',
  'time',
  'evalRate',
  'b01',
  'b10',
  'pairedP',
  'delta',
  'overfit',
  'avgWinTicks',
  'kills',
  'dmgPerKill',
  'residualHp',
  'loot',
  'scoreMean',
  'evalSec',
  'wver',
]

/** 按顺序取列定义（渲染器用它同时生成表头与列数，避免两处各数一遍）。 */
export function colsOf<K extends MetricColKey>(keys: readonly K[]): MetricCol<K>[] {
  return keys.map((k) => METRIC_COLS[k] as MetricCol<K>)
}
