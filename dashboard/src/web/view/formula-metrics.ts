/** formula-metrics.ts — 「这个指标在 shaping formula 里叫什么」（用户 2026-11-10）。
 *
 *  为什么需要它：课程文件的 `reward.formula` 只写账本英文字面量（`wKill*kills`、
 *  `-wThreat*min(threatTicks, threatCap)`…），而控制台的表头/图例是中文。看曲线时想知道
 *  「它到底进没进公式、公式里叫什么」，只能去翻 jsonc。这里给出唯一一份对照，走势图 tooltip
 *  与指标表 hover 都从它取（两处不许各写一种说法）。
 *
 *  名字的 SSOT 是 Python：`nn-training/biz/reward_library.py::METRICS`（90 列；公式里能写的
 *  指标符号 = `METRIC_INDEX` 的键）。本模块**不复制**那 90 个字符串：逐局指标注册表
 *  `BEHAVIOR_METRICS` 本就按同一顺序镜像它（见 `behavior-metrics.ts` 头注释），减去两个
 *  eval 专属读数（rollout 侧没有该列 ⇒ 公式里写不到）即可。
 *  `tests/web-formula-metrics.test.ts` 钉住「恰好 90 个」——将来新增一个不进公式的列，
 *  该测试会红，逼人回来做一次判断（而不是让它静默冒充公式变量）。
 *
 *  只给**真能写进公式**的名字：胜率 / 胜局耗时 / 歼灭率这些是跨局聚合，不是 metrics 向量里
 *  的列 ⇒ 返回 null（不冒充）。少数聚合图确实是某个 metrics 列的每局平均（`killsAbs`、
 *  `pu`），那是一一对应的，照实标。
 */

import { BEHAVIOR_METRICS, type BehaviorMetricKey } from './behavior-metrics'

/** eval 专属读数（`reward_library.METRICS` 里没有这两列 ⇒ 公式里写不到）。 */
export const NON_FORMULA_METRIC_KEYS: readonly BehaviorMetricKey[] = [
  'aliveTicks',
  'postThawDamageRate',
]

/** 可用于 formula 的指标名（顺序 = 账本列序；公式里可用的符号就是这些）。 */
export const FORMULA_METRIC_NAMES: readonly string[] = BEHAVIOR_METRICS.map((m) => m.key).filter(
  (k) => !NON_FORMULA_METRIC_KEYS.includes(k),
)

const FORMULA_SET: ReadonlySet<string> = new Set<string>(FORMULA_METRIC_NAMES)

/** 是不是公式里可写的指标符号（纯查表）。 */
export function isFormulaMetric(name: string): boolean {
  return FORMULA_SET.has(name)
}

/** 少数「聚合走势/表格键」→ 它逐局平均的那个 metrics 列。
 *  只收**同一个量**的映射：歼灭率是 Σkills/Σ敌数、残血是胜局残血……都不是 metrics 的列，
 *  不在表里（宁可不说，也不让公式对照说谎）。 */
const AGGREGATE_FORMULA: Readonly<Record<string, string>> = {
  killsAbs: 'kills', // 每局平均击杀 = mean(kills)
  pu: 'powerUpsCollected', // 每局平均拾取 = mean(powerUpsCollected)
}

/** 任意指标键 → formula 里的名字；没有对应 ⇒ null。
 *
 *  认的形状（三种调用方各自的键都会走到这里，映射只有一份）：
 *   · 账本键 `ticks`（逐局指标定义 / cells）；
 *   · 列键 `bm:ticks`（指标表的逐局指标列）；
 *   · 走势键 `behavior-ticks-mean-eval`（`behaviorMetricSeries` 生成的 Series.key）；
 *   · 聚合走势键 `killsAbs` / `pu`（见 AGGREGATE_FORMULA）。
 */
export function formulaMetricName(key: string): string | null {
  const mapped = AGGREGATE_FORMULA[key]
  const direct = mapped ?? key
  if (FORMULA_SET.has(direct)) return direct
  if (key.startsWith('bm:')) {
    const k = key.slice(3)
    return FORMULA_SET.has(k) ? k : null
  }
  if (key.startsWith('behavior-')) {
    // 走势键 = `behavior-<metric>-<stat>-<source>`；指标名是 camelCase，不含 '-'
    const k = key.slice('behavior-'.length).split('-')[0]
    return FORMULA_SET.has(k) ? k : null
  }
  return null
}

/** hover 里的统一写法（调用方只拼这一句，图和表不会各写一种标点）。 */
export function formulaLabel(name: string): string {
  return `formula: ${name}`
}
