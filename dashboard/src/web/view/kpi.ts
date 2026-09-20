/** kpi.ts — KPI 条的**读面**：6 格「一眼看完」的关键读数（问题 C1 的解法之一）。
 *  纯函数，无 IO、无 localStorage、不读墙钟（`nowMs` 由调用方给）。
 *
 *  ## 为什么要一条 KPI 条
 *
 *  合并前首页把「现在能不能跑 / 这轮跑到哪了」这两个问题摊在 12 个平权面板里：胜率埋在
 *  趋势图右上角的数字里、iter 埋在 Hero 表格首行、在训课程数埋在总览卡表头、节点在线数
 *  埋在节点 pill 行、队列深度埋在调度器表头。操作员回答「现在什么情况」的方式是**滚动 + 读数**。
 *
 *  KPI 条把**已经存在于别处的**六个读数提到首屏同一行（不新增服务端契约、不新算任何判据）：
 *  它是**索引**，不是第二套真相——每一格都能点进承载它的那一页/那一面板看细节。
 *
 *  ## 三条硬纪律
 *
 *  1. **不知道 ≠ 0**。hub 无应答时队列深度是「不知道」，值渲染 `—` 并在 `sub` 里说明原因
 *     （与 `course-matrix.ts` 的 `CELL_UNKNOWN` 同一条纪律）。把「不知道」画成 0 会让操作员
 *     以为「队列空 = 一切正常」。
 *  2. **口径写进 `title`**。每格的数字都有一句悬停说明它从哪来、怎么算——KPI 条最容易出的
 *     问题就是「数字对不上别处」，而对不上的原因从来是口径没说清。
 *  3. **不静默**。没有数据的格子渲染 `—` + 原因（`sub`），**不返回 null**（C13：面板不许
 *     整块消失）。
 */

import type { ConsoleStateView } from './console-types'
import { fmtElapsed, fmtPct } from './format'
import type { IterRow } from './metric-types'
import { latestRow, winTone } from './rows'
import { metricSeries, sliceSeries } from './series'
import type { PageKey } from './routes'
import type { StatusTone } from '../components/StatusDot'

/** KPI 值的语义档（复用 `StatusDot` 的四档词表：同一语义全站同色）。 */
export type KpiTone = StatusTone

export interface KpiTile {
  /** 稳定 id（渲染 key + 用例定位）。 */
  id: 'win' | 'eval' | 'phase' | 'courses' | 'fleet' | 'sched'
  /** 短标签（≤6 字，窄屏不换行）。 */
  label: string
  /** 大号值（已格式化；未知 = `—`）。 */
  value: string
  /** 单位/后缀（小号接在值后）。 */
  unit?: string
  /** 值下方的一行副读数（变化量 / 上下文；**未知时必须说明为什么**）。 */
  sub: string
  /** 微型走势取值（可空：这格没有时序就不画，不编一条平的假的）。 */
  spark?: number[]
  tone: KpiTone
  /** 悬停口径（这一格的数字从哪来 / 怎么算）。 */
  title: string
  /** 点击深链（承载这一格细节的那一页；省略 = 不可点）。 */
  to?: PageKey
}

/** KPI 条上「值未知」的统一占位符（与课程矩阵的 `CELL_UNKNOWN` 同字：全站只有一个「—」）。 */
export const KPI_UNKNOWN = '—'

/** 只看真实迭代（`iter > 0`，时间正序）。
 *
 *  排除 `iter === 0`：控制台会合成一条 it0 行（bc 权重基线），它只有干净评估、
 *  rollout 派生字段全是 NaN 缺口——把它的胜率当「最新采样胜率」是错的（Hero 的
 *  `heroMainRows` 出于同一理由做同一个过滤）。 */
function realRows(iters: IterRow[]): IterRow[] {
  return iters.filter((r) => r.iter > 0).sort((a, b) => a.iter - b.iter)
}

/** 最新一条**带干净评估**的轮（倒序扫；无 → null）。 */
function latestEvalRow(iters: IterRow[]): IterRow | null {
  let best: IterRow | null = null
  for (const r of iters) {
    if (!r.evalData || r.evalData.winRate === null) continue
    if (!best || r.iter > best.iter) best = r
  }
  return best
}

/** 百分点差值展示：`+3.2pp` / `-1.0pp` / `±0.0pp`（0 也带符号说明方向无关）。 */
function fmtDeltaPp(from: number, to: number): string {
  const pp = (to - from) * 100
  const sign = pp > 0 ? '+' : pp < 0 ? '' : '±'
  return `${sign}${pp.toFixed(1)}pp`
}

/** 值档 → 类名后缀（`ok`/`warn`/`err`/`off`）。导出给组件与用例共用，避免两处各写一遍。 */
export function kpiToneClass(tone: KpiTone): string {
  return `tc-kpi__val--${tone}`
}

/** `winTone`（绿/黄/红）→ KPI 三档（KPI 没有「不知道」之外的中立态：胜率总是有值或没值）。 */
function winRateTone(wr: number): KpiTone {
  const t = winTone(wr)
  return t === 'g' ? 'ok' : t === 'y' ? 'warn' : 'err'
}

/**
 * 六个 KPI 格（顺序即渲染顺序：先「练得怎么样」，再「跑到哪了」，最后「算力/调度」）。
 *
 *  输入**只有整页快照**（`ConsoleStateView`）：这六个数在合并前就已经存在于快照里，
 *  本函数只是把它们挑出来、格式化、给出未知态——**没有任何新计算**（新判据就意味着
 *  新的漂移点：同一条语义两处各算一遍，迟早各说各话）。
 */
export function kpiTiles(state: ConsoleStateView | null, nowMs: number): KpiTile[] {
  if (!state) return []
  const iters = state.metrics.iters ?? []
  const real = realRows(iters)
  const head = real.length > 0 ? real[real.length - 1]! : null
  const prev = real.length > 1 ? real[real.length - 2]! : null
  const latest = latestRow(iters)
  const series = metricSeries(iters)

  return [
    winTile(head, prev, series),
    evalTile(latestEvalRow(iters), series),
    phaseTile(latest, state, nowMs),
    coursesTile(state),
    fleetTile(state),
    schedTile(state),
  ]
}

/** ① 采样胜率（rollout 口径）+ 与上一轮差值。 */
function winTile(
  head: IterRow | null,
  prev: IterRow | null,
  series: ReturnType<typeof metricSeries>,
): KpiTile {
  const spark = series.find((s) => s.key === 'winRate')
  return {
    id: 'win',
    label: '采样胜率',
    value: head ? fmtPct(head.winRate) : KPI_UNKNOWN,
    sub: head
      ? prev
        ? `Δ ${fmtDeltaPp(prev.winRate, head.winRate)} vs it${prev.iter}`
        : `首轮（it${head.iter}）`
      : '该课程暂无迭代记录',
    spark: spark ? sliceSeries(spark, '30').vals : undefined,
    tone: head ? winRateTone(head.winRate) : 'off',
    title: head
      ? `第 ${head.iter} 轮 rollout 采样胜率（训练时实际对局的胜率，含探索噪声）· ${head.samples} 局；` +
        '涨幅 = 与本课程上一真实轮之差。它与 eval 胜率的差就是「练熟样本」的指纹。'
      : '尚无真实迭代（it>0）——还没有一轮训练跑完',
    to: 'metrics',
  }
}

/** ② 干净评估胜率 + 与同轮采样胜率的 gap（过拟合信号）。
 *
 *  gap 用**同一轮**的采样胜率做被减数（不是「全局最新轮」）：不同轮的对比会把轮间波动
 *  读成过拟合，而 eval 每几轮才跑一次，那个差常常是纯噪声。 */
function evalTile(row: IterRow | null, series: ReturnType<typeof metricSeries>): KpiTile {
  const spark = series.find((s) => s.key === 'eval')
  const e = row?.evalData ?? null
  const wr = e?.winRate ?? null
  const gap = wr !== null && row ? fmtDeltaPp(row.winRate, wr) : null
  return {
    id: 'eval',
    label: 'eval 胜率',
    value: wr !== null ? fmtPct(wr) : KPI_UNKNOWN,
    sub:
      wr !== null && e
        ? `对采样 ${gap} · ${e.wins}/${e.games} 局`
        : '尚无干净评估（eval 每 N 轮才跑一次）',
    spark: spark ? sliceSeries(spark, '30').vals : undefined,
    tone: wr !== null ? winRateTone(wr) : 'off',
    title:
      wr !== null && row
        ? `第 ${row.iter} 轮 in-loop 干净评估（greedy 固定语料）· ${e?.games} 局 ${e?.wins} 胜。` +
          '与**同一轮**采样胜率之差：eval 明显低于采样 = 在训练分布上过拟合（或采样在探索中偶然得手）。'
        : '该课程还没有一轮带干净评估的迭代——评估窗口尚未跑过',
    to: 'metrics',
  }
}

/** ③ 当前 iter / 阶段（+ 已走时长）。
 *
 *  与顶栏阶段 chip 是同一份事实（`state.phase`，同源自训练循环日志尾）：顶栏那个属于
 *  外壳（跨页常驻、空闲时不显示），这一格属于总览的读数网格。两条都留，因为「我在总览
 *  看这一屏」时不该去左上角找数字。 */
function phaseTile(latest: IterRow | null, state: ConsoleStateView, nowMs: number): KpiTile {
  const ph = state.phase
  const running = ph.phase === 'rollout' || ph.phase === 'ppo'
  const elapsed = running && ph.sinceMs !== null ? fmtElapsed(nowMs - ph.sinceMs) : null
  const label = ph.phase === 'rollout' ? '采集' : 'PPO'
  return {
    id: 'phase',
    label: '当前阶段',
    value: latest ? `it${latest.iter}` : KPI_UNKNOWN,
    sub: running ? `${label} · 已走 ${elapsed ?? KPI_UNKNOWN}` : '空闲（没有在跑的阶段）',
    tone: running ? 'ok' : 'off',
    title:
      '轮次 = 账本最新一条 iteration（it0 是 bc 基线合成行）。阶段判据 = 训练循环日志尾：' +
      '「=== iteration N/M ===」= 采集、「rollout itN:」及之后 = PPO。空闲可能是没在训，' +
      '也可能是日志已轮转——去 /log/trainingLoop 看真日志。',
  }
}

/** ④ 在训课程 `n/N`（+ 「另有 M 门在别处训练」）。
 *
 *  「另有 M 门」这段话是这一格存在的**主要理由**：单课程时代「在训 1/1」就够了，多课程
 *  并行后最危险的局面恰好是「你在看的这门停着，而别处有几门在烧算力」——一个只有分母
 *  没有归属的 `0/5` 读不出这件事。 */
function coursesTile(state: ConsoleStateView): KpiTile {
  const all = state.courses ?? []
  // 在训清单以服务端 stamp 的 `trainingCourses` 为准；旧视图缺这个键时回退调度器口径
  // （`trainingCount` 与它是同一份事实的两个出口，见 loop-queue.ts::withTraining）。
  const named = state.trainingCourses
  const n = named ? named.length : (state.loopQueue?.trainingCount ?? 0)
  const viewing = state.course
  // 逐课归属只有 `trainingCourses` 知道：缺这个键时**不能**替操作员猜 M（猜出来的
  // 「另有 M 门在别处」可能把当前课自己算进去），那一档老实说「名单未知」。
  const elsewhere = named ? (named.includes(viewing) ? n - 1 : n) : 0
  const sub =
    all.length === 0
      ? '未发现任何课程'
      : n === 0
        ? '没有在训的课程'
        : !named
          ? `在训 ${n} 门（旧视图无逐课名单）`
          : elsewhere > 0
            ? `另有 ${elsewhere} 门在别处训练`
            : '含当前查看的课'
  return {
    id: 'courses',
    label: '在训课程',
    value: `${n}/${all.length}`,
    sub,
    tone: n > 0 ? 'ok' : 'off',
    title:
      '分子 = 共享 trainer 在跑且这门课未收官（registry 进程存活 ∧ python 队列状态）；' +
      `分母 = 可发现的课程数（${all.length} 门）。当前查看：${viewing || '（未选课程）'}。` +
      '逐课的状态与冲突（hub 未注册 / hub 已注册但无进程）看下方课程矩阵。',
  }
}

/** ⑤ 算力：启用节点在线 `x/y` + 本机直跑槽位。
 *
 *  分母只算**启用**的节点：停用的节点本来就不该在线，把它算进分母会让「3/4 在线」这种
 *  正常局面永远显示成不满。 */
function fleetTile(state: ConsoleStateView): KpiTile {
  const enabled = (state.nodes ?? []).filter((nd) => nd.enabled)
  const online = enabled.filter((nd) => nd.online === true).length
  const slow = enabled.filter((nd) => nd.online === false && nd.slow).length
  const local = state.localNode ?? null
  const localTxt = local
    ? local.slots > 0
      ? `本机 ${local.slots} 槽`
      : '本机直跑未启用（0 槽）'
    : '本机直跑未启用'
  const subParts: string[] = []
  if (enabled.length > 0) subParts.push(`${online}/${enabled.length} 在线`)
  if (slow > 0) subParts.push(`${slow} 慢`)
  subParts.push(localTxt)
  return {
    id: 'fleet',
    label: '算力',
    value: enabled.length > 0 ? `${online}/${enabled.length}` : KPI_UNKNOWN,
    sub: subParts.join(' · '),
    tone: enabled.length === 0 ? 'off' : online < enabled.length ? 'warn' : 'ok',
    title:
      `已在册并启用的远端 worker 节点 ${enabled.length} 个，其中 ${online} 个此刻 ping 通` +
      `（分母不含停用节点——它们本来就不该在线）。${localTxt}。` +
      '逐节点贡献与登记详情见「节点」页。',
    to: 'nodes',
  }
}

/** ⑥ 调度：队列深度（待领 job）+ 在飞 + 排队等资源的课程数。
 *
 *  队列与在飞都是 **hub 侧**事实：hub 无应答时它们是「不知道」，不是 0。 */
function schedTile(state: ConsoleStateView): KpiTile {
  const ov = state.overview ?? null
  const lq = state.loopQueue ?? null
  const blocked = lq?.blockedCourses.length ?? 0
  if (!ov || !ov.hubOnline) {
    return {
      id: 'sched',
      label: '队列',
      value: KPI_UNKNOWN,
      unit: '待领 job',
      sub: !ov ? 'hub 视图未读（队列不可知）' : 'hub 无应答（队列不可知）',
      tone: 'warn',
      title:
        'hub 的 /admin/queue 读不到 ⇒ 队列深度与在飞**不可知**（不是 0）。' +
        '训练侧仍在跑，但 PPO job 不会被派发——先起/修 hub-server。',
    }
  }
  let pending = 0
  let inflight = 0
  for (const r of ov.rows) {
    pending += r.queuePending
    inflight += r.inflight
  }
  const subParts = [`在飞 ${inflight}`]
  if (blocked > 0) subParts.push(`等票 ${blocked} 门`)
  if (!lq) subParts.push('调度器视图未读')
  return {
    id: 'sched',
    label: '队列',
    value: String(pending),
    unit: '待领 job',
    sub: subParts.join(' · '),
    tone: blocked > 0 ? 'warn' : 'ok',
    title:
      `hub 全部课程的待领 job 合计 ${pending} 条（可领取 = 有 worker 能领走），` +
      `在飞 ${inflight} 条（已发布未回传）。` +
      (blocked > 0
        ? `本机重资源池满了：${lq?.blockedCourses.join('、')} 在排队等票（单进程调度器的正常态，不是卡死）。`
        : '本机重资源池无排队。') +
      '逐课拆分见下方课程矩阵。',
  }
}
