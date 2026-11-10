/** metric-columns.ts — **逐轮指标列的唯一模型**（表头文字 / hover 口径 / 数字对齐）。
 *
 *  为什么需要它（docs/dashboard-redesign.md §6 P3c 与 DECISIONS §2026-09-20-metric-table-single-home）：
 *  同一套逐轮指标被渲染在两个地方——首页 Hero 的「最新 6 轮」速览表，与 `/metrics` 的完整表
 *  （`DataTable`，带搜索/排序/列显隐）。两个渲染器的**结构**本来就该不同（速览 vs 分析），
 *  但**列模型**不该有两份：此前两处的表头文字与 hover 口径各写一遍，能一致纯靠人手抄得仔细
 *  ——改一处漏一处只是时间问题。
 *
 *  本仓已有正确先例：配对列（b01/b10/p/delta）的 hover 文案早就抽到 `view/format.ts`
 *  由两处共用（`PAIRED_COL_TITLES`，注释写着「防两处分化」）。本模块把
 *  同一条做法扩到全部列。
 *  （2026-10-22：双轨退场 ⇒ 「过拟合」列与它的 hover 文案 `OVERFIT_COL_TITLE` 一起删了。）
 *
 *  三个不变量（改列时按它判断）：
 *    1. **label / title 只在这里写一次**——两个面板都不许再出现表头字面量。
 *    2. **列顺序也只在这里**（`ROLLOUT_ROW_KEYS` / `EVAL_ROW_KEYS`）；渲染器只按顺序摆 `<td>`，
 *       不许自己决定「哪一列在前」。
 *    另：首页速览表的**备选列** = 完整指标表的列（用户 2026-11-10「完全一样」）——两处都从
 *    `app/panels/metric-table-cols.tsx` 的同一个列工厂取，本文件不再保留首页专用列。
 *    3. `num` 同时决定右对齐与 `tabular-nums`（数字列的两位一体，避免一处对齐一处不齐）。
 *
 *  本模块纯数据 + 纯函数（无 JSX / 无 IO），与其它 `view/` 模块同规：服务端与客户端共用。
 */

import {
  BEHAVIOR_METRICS,
  METRIC_STAT_LABEL,
  type BehaviorMetricKey,
  type MetricStatKey,
  type MetricStats,
} from './behavior-metrics'
import { PAIRED_COL_TITLES } from './format'

// ────────────────────────── 键 ──────────────────────────

/** 主行（rollout 口径）表的列键：完整指标表的 all / rollout 档按它排（顺序见 `ROLLOUT_ROW_KEYS`）。 */
export type RolloutRowColKey =
  | 'iter'
  | 'time'
  | 'winRate'
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
  | 'window'
  | 'stageProgress'

/** eval（干净评估）表的列键（顺序见 `EVAL_ROW_KEYS`）。
 *
 *  与主行表的差异是**真的语义差异**，不是排版差异：eval 表看的是「这一轮干净评估有多可信」，
 *  所以多了配对四列（b01/b10/p/delta），少了 PPO 诊断列（KL/熵/mean_ret/lr——它们与
 *  eval 无关），胜率列换成 `evalRate`。 */
export type EvalRowColKey =
  | 'iter'
  | 'time'
  | 'evalRate'
  | 'b01'
  | 'b10'
  | 'pairedP'
  | 'delta'
  | 'avgWinTicks'
  | 'kills'
  | 'dmgPerKill'
  | 'residualHp'
  | 'loot'
  | 'scoreMean'
  | 'evalSec'
  | 'wver'

/** 逐局指标列键：用 `bm:` 前缀避开与既有列（如 `kills`/`ticks`）的键冲突。 */
export type BehaviorMetricColKey = `bm:${BehaviorMetricKey}`

/** 全部列键（两张表共用这一份并集）。 */
export type MetricColKey = RolloutRowColKey | EvalRowColKey | BehaviorMetricColKey

// ────────────────────────── 模型 ──────────────────────────

export interface MetricCol<K extends MetricColKey = MetricColKey> {
  key: K
  /** 表头文字（唯一一份）。 */
  label: string
  /** 逐局指标列对应的账本键（表头键带 `bm:` 前缀，账本键不带）。 */
  behaviorKey?: BehaviorMetricKey
  /** 表头 hover 口径（大白话；缺省 = 这一列不必解释）。 */
  title?: string
  /** 数字列：右对齐 + `tabular-nums`（竖着比大小用）。 */
  num?: boolean
  /** 默认隐藏（首次上屏 / 坏存储）：逐局指标列；两张表的「列配置」都可打开。 */
  hiddenByDefault?: boolean
}

/** 逐局指标列的键前缀（指标表配置弹窗与 `Col.key` 共用这一份；账本键不带前缀）。 */
export const BEHAVIOR_COL_PREFIX = 'bm:'

/** 账本键 → 指标表列键。 */
export function behaviorColKey(k: BehaviorMetricKey): BehaviorMetricColKey {
  return `${BEHAVIOR_COL_PREFIX}${k}`
}

/** 指标表列键 → 账本键；不是逐局指标列 ⇒ null。 */
export function behaviorColKeyOf(key: string): BehaviorMetricKey | null {
  return key.startsWith(BEHAVIOR_COL_PREFIX)
    ? (key.slice(BEHAVIOR_COL_PREFIX.length) as BehaviorMetricKey)
    : null
}

/** 行为指标列（表内统计量可在「指标配置」里切换，缺省均值；六种全都见单元格 hover）。 */
const BEHAVIOR_METRIC_COLS = Object.fromEntries(
  BEHAVIOR_METRICS.map((m) => {
    const key: BehaviorMetricColKey = behaviorColKey(m.key)
    return [
      key,
      {
        key,
        behaviorKey: m.key,
        label: m.label,
        title: `逐局指标统计（表内统计量可在「指标配置」里切换）· ${m.title}`,
        num: true,
        hiddenByDefault: true,
      },
    ]
  }),
) as Record<BehaviorMetricColKey, MetricCol<BehaviorMetricColKey>>

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
    title: '胜局剩余总血量 / 单命满血（三命全在 = 300%）',
    num: true,
  },
  loot: { key: 'loot', label: '道具', title: '每局平均拾取数/掉落数', num: true },

  // ── 采样范围（动态采集/轮换专属；读数一律从账本搬运，不重算） ──
  window: {
    key: 'window',
    label: '窗口',
    title:
      '本轮采样子集 = 窗口关数 / --stages 全关数（rotation_mode 只采窗口里的关）；hover 列关号',
  },
  stageProgress: {
    key: 'stageProgress',
    label: '各关进度',
    title: '本轮逐关配额进度（达标关数 / 窗口关数）；hover 列逐关 收到/达标线/缺口',
  },

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

  // ── eval 专属 ──
  evalSec: {
    key: 'evalSec',
    label: '用时',
    title: '评估窗口用时（该轮 eval 从开始到收齐）',
    num: true,
  },
  wver: { key: 'wver', label: 'wver', title: '评估所用权重版本（显示前 7 位）' },

  // ── 逐局指标（完整 metrics 列；默认隐藏，首页与完整表都可在「列配置」里打开） ──
  ...BEHAVIOR_METRIC_COLS,
}

/** 主行（rollout 口径）的列顺序。**顺序只在这里**（渲染器不再自己决定先后）。
 *  完整指标表的 all / rollout 档与首页速览表的主行视图共用这一份（逐局指标块压在其后）。 */
export const ROLLOUT_ROW_KEYS: readonly RolloutRowColKey[] = [
  'iter',
  'time',
  'winRate',
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
  'window',
  'stageProgress',
]

/** eval 视图的列顺序（完整指标表的 eval 档与首页速览表的 eval 视图共用这一份）。 */
export const EVAL_ROW_KEYS: readonly EvalRowColKey[] = [
  'iter',
  'time',
  'evalRate',
  'b01',
  'b10',
  'pairedP',
  'delta',
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

// ────────────────────────── 指标表的列显隐（用户 2026-11-10） ──────────────────────────

/** 默认隐藏的列（= 模型里 `hiddenByDefault` 的那些）：首次上屏与坏存储都退到这里。 */
export const DEFAULT_HIDDEN_COL_KEYS: readonly string[] = (
  Object.keys(METRIC_COLS) as MetricColKey[]
).filter((k) => METRIC_COLS[k].hiddenByDefault)

/** 列显隐偏好的持久化值（JSON 字符串数组）→ 隐藏集合。
 *
 *  · 新键有值 ⇒ 只认它（用户手点的结果就是全部真相）；
 *  · 新键缺/坏、有**旧键**（DataTable 时代 `tc.metrics.hidden`）⇒ 迁移：旧集合 ∪ 默认集合
 *    ——旧键存的时候还没有逐局指标列，不补默认会让 90+ 列一起上屏；
 *  · 都没有 / 都是坏值 ⇒ 默认集合。
 *
 *  `defaults` 可被调用方改：首页速览表默认一列都不藏（传 `[]`），指标页默认藏逐局指标列。
 *
 *  为什么放 `view/`：与 Hero 的走势偏好同一套路——首帧走默认，hydrate 后由纯函数解析存储。 */
export function parseHiddenCols(
  raw: string | null | undefined,
  legacy?: string | null,
  defaults: readonly string[] = DEFAULT_HIDDEN_COL_KEYS,
): Set<string> {
  const parse = (s: string | null | undefined): string[] | null => {
    if (!s) return null
    try {
      const v = JSON.parse(s) as unknown
      if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) return null
      return v as string[]
    } catch {
      return null
    }
  }
  const fresh = parse(raw)
  if (fresh) return new Set(fresh)
  const old = parse(legacy)
  if (old) return new Set([...old, ...defaults])
  return new Set(defaults)
}

/** 逐局指标列的表头文字：统计量非均值时带后缀（表里放的是中位数/分位数，不能只靠 hover 才知道）。 */
export function metricColLabelWithStat(label: string, stat: MetricStatKey): string {
  return stat === 'mean' ? label : `${label}·${METRIC_STAT_LABEL[stat]}`
}

/** 逐局指标列的**表内读数** = 配置的那一种统计量（缺读数 ⇒ null，渲染显 `—`，不冒充 0）。
 *  单元格与排序共用它：改了统计量，显示的数和排出来的序一起变。 */
export function metricStatValue(
  st: MetricStats | null | undefined,
  stat: MetricStatKey,
): number | null {
  const v = st?.[stat]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}
