/** course-matrix.ts — 课程矩阵：把「并行课程总览」（hub 侧）与「训练调度器」（训练侧）
 *  按课程名 **outer join** 成一张表（问题 C5）。纯函数，无 IO、无 node:、无 Bun。
 *
 *  ## 为什么必须合并
 *
 *  这两张表回答的是**同一个问题**——「这门课现在怎么样」——却各写了一半，而且两半**互相看不见**：
 *
 *  - 「并行课程总览」知道 hub 侧事实：这门课在不在 hub 课程表里、是离线还是在线、队列积压多少、
 *    离线段回传了多少轮。
 *  - 「训练调度器」知道训练侧事实：账本指针、这一轮卡在 13 步表的哪一步、在等谁回传。
 *
 *  拆开看时，**最该看见的信号恰好两边都看不见**：hub 在给一门**没有任何进程推进**的课派活
 *  （job 越堆越多，没人消费），或者一门课在**疯狂训练但 hub 根本没注册它**（PPO 永远不被派发，
 *  算力白烧）。前者的每一半在各自表里都是正常的：hub 表上它是「在线」，训练表上它只是「未在训」
 *  ——一个看起来完全平静的行。这正是本模块存在的理由。
 *
 *  ## 上屏的行：**只列在训课程**（2026-09-20 用户指令）
 *
 *  合并规则产出的仍是**全集**（outer join 不丢任何一门课——纯函数层不筛），筛在面板侧发生：
 *  `CourseMatrix` 用 `isTrainingRow` 过滤，未在训的行**不渲染**（其计数与逐行状态在表头 chip 的
 *  悬停里，不静默丢）。
 *
 *  为什么（用户的读序）：这门表的用途是「盯着正在跑的那几门」，而未在训的课在盘上可能积到几十门
 *  （历史课都有账本与目录）——它们把在训的那几行挤到屏幕外。**代价**：`hub 已注册 · 无进程`
 *  这种「两半事实打架」的行（合并的初衷之一，见上）不再直接上屏，只能从表头 chip 的计数看出
 *  「有几门没列」——这是刻意用**读噪**换**信息密度**（DECISIONS §2026-09-20-console-declutter）。
 *
 *  ## 合并规则（三条，都是「不编数据」的推论）
 *
 *  1. **并集做主键**：任一侧有的课程都要出行。只在 hub 表里（训练侧还不认识它）或只在训练侧
 *     （hub 没注册）都是**真实存在的局面**，不是脏数据。
 *  2. **单侧缺失的列渲染 `—` + 表头说明原因**，而不是 0 或空：`hub 无应答` 与 `队列 0` 是
 *     两件完全不同的事（前者不知道，后者确定没有）。
 *  3. **冲突上状态列**（多对一映射见 `matrixStatus`），不吞不藏。
 */

import type { CourseOverviewRow, ParallelOverviewView } from './course-overview'
import { fmtRel } from './format'
import {
  type LoopQueueRow,
  type LoopQueueView,
  STOPPED_TITLE,
  pauseBadge,
  pauseLabel,
  pauseTitle,
  waitCls,
  waitTitle,
} from './loop-queue'
import type { RowBadge } from '../components/StatusRow'
import type { StatusTone } from '../components/StatusDot'

/** 段内产物「多久没动了」的红线：超过它就当可疑（离线课挂掉与还在跑的唯一区分）。
 *  离线段是整段上云（一轮可能十几分钟），30 分钟仍属正常长轮，故定 1h。 */
export const OFFLINE_STALE_SEC = 3600

// ────────────────────────── 状态判定 ──────────────────────────

/** 状态的档位（多对一映射到 `StatusDot` 的四档，映射的**理由**写在 `matrixStatus` 里）。
 *
 *  `info` 是四档之外的第五种**语义**：`off`（未在训）表示「没在干活」，而 `info` 表示
 *  「在干活、只是被 hub 标成了离线模式」——两者都不该涂成黄/红，但混成同一个灰点会把
 *  「离线回传中」读成「停着」。 */
export type MatrixTone = StatusTone | 'info'

/** 语义档 → 状态点档（`info` 归 `off`：它不是告警，点不必抢眼）。 */
export function matrixDotTone(tone: MatrixTone): StatusTone {
  return tone === 'info' ? 'off' : tone
}

/** 语义档 → 徽章档（`StatusRow.RowBadge` 的 `tc-badge--*` 词表：g/y/r/a/gray）。 */
export function matrixBadgeTone(tone: MatrixTone): NonNullable<RowBadge['tone']> {
  switch (tone) {
    case 'ok':
      return 'g'
    case 'warn':
      return 'y'
    case 'err':
      return 'r'
    case 'info':
      return 'a'
    default:
      return 'gray'
  }
}

export interface MatrixStatus {
  text: string
  tone: MatrixTone
  /** 悬停全文：这一行**为什么**是这个状态。 */
  title: string
}

/** 两侧判据互相矛盾时的归类（`null` = 没有矛盾）。
 *
 *  - `hub-unregistered`：训练侧在跑，hub 的课程表里却没有它 ⇒ PPO job 永远不会被派发。
 *  - `hub-no-process`：hub 在给它派活，训练侧却没有进程推进它 ⇒ job 堆着没人消费。 */
export type MatrixConflict = 'hub-unregistered' | 'hub-no-process' | null

/** 表头 hover：hub 侧的元信息缺失**不等于**「这门课不在 hub 表里」。 */
export const HUB_DOWN_TITLE =
  '没有任何 hub 在应答 /admin/queue——队列列与「hub 是否注册」都**不可知**（不是「未注册」）。先启 hub-server'

/** 表头 hover：训练侧只读视图缺失。 */
export const QUEUE_DOWN_TITLE =
  '训练侧只读视图 run_rl_cluster.py --json 不可用——指针与「在等什么」不可知（不是「无待办」）'

/** 单侧缺失时单元格里的占位符。**不是 0**：0 是「确定没有」，`—` 是「不知道」。 */
export const CELL_UNKNOWN = '—'

/** **「在训」判据**（本模块唯一出处）：两侧同源（都出自「调度器存活 ∧ 该课未收官」），
 *  任一侧给了就用——不存在两说。
 *
 *  `ov` 先于 `lq` 取：hub 侧的 `training` 是会话级快照（`stateView.trainingCourses` 推导），
 *  训练侧的是每拍队列读数；两者应当一致，不一致时以 hub 侧为准（合并前两张表就是这么读的）。 */
export function rowTraining(ov: CourseOverviewRow | null, lq: LoopQueueRow | null): boolean {
  return ov?.training ?? lq?.training ?? false
}

/** 行级包装：面板按它筛「哪些行上屏」（课程区只列在训课程）。
 *
 *  ★ 与 `matrixStatus` 用**同一个**判据：状态列说「在训」而这一行没上屏（或反过来）就是 bug。 */
export function isTrainingRow(r: CourseMatrixRow): boolean {
  return rowTraining(r.ov, r.lq)
}

/**
 * 一行 → 状态。优先级即「哪条信息最该先说」，与合并前两张表各自的顺序保持一致
 * （唯一的**新增**是第 4 档：hub 已注册但没进程——从前它落在「停」里，看不出区别）。
 *
 * 1. 在训但 hub 没注册（warn）——算力在烧，job 永远不派发。
 * 2. hub 标了离线（info）——**这不是故障**，是 hub 的一种合法模式（只收回传），故不高亮成黄/红。
 * 3. 在训（ok）。
 * 4. hub 在线且注册了它，却没有进程（warn）——job 会堆起来。
 * 5. 其余：未在训（off）。
 *
 * ⚠ 第 1、4 档**必须**以 `hubOnline` 为前提：hub 无应答时 `hubSeen` 恒为 false（队列整个读不到），
 * 拿它当「hub 不认这门课」是**假诊断**——面板表头明明写着「hub 无应答」，行里却叫人去用
 * `--course` 重启 hub。合并前的总览卡就犯了这个错。
 */
export function matrixStatus(
  ov: CourseOverviewRow | null,
  lq: LoopQueueRow | null,
  /** hub 此刻在不在应答——**每行事实之外的机群级事实**，故由调用方显式给：
   *  `hubSeen === false` 只有在 hub 在线时才读作「hub 不认这门课」，否则是「不知道」。 */
  hubOnline: boolean,
): MatrixStatus {
  // 「在训」的判据两侧同源（都出自调度器存活 ∧ 该课未收官），任一侧给了就用——不存在两说。
  const training = rowTraining(ov, lq)
  const hubKnown = ov !== null && hubOnline
  const hubSeen = hubKnown && ov.hubSeen

  if (training && hubKnown && !ov.hubSeen) {
    return {
      text: '在训 · hub 未注册',
      tone: 'warn',
      title:
        'hub 的课程表里没有这门课——它的 PPO job 永远不会被派发（rollout 白跑）。' +
        '以 `--course <课>` 重启 hub，或把 hub 的课程表补上这门课',
    }
  }
  if (ov?.offline) {
    return {
      text: '离线（只收回传）',
      tone: 'info',
      title: 'hub 把这门课标为离线：不实时派发 PPO，只接收 it 权重/指标回传（本机训练与账本不动）',
    }
  }
  if (training) {
    return {
      text: '在训',
      tone: 'ok',
      title:
        '共享 trainer 在跑，且这门课未收官（进程存活 = registry，还算不算活 = python 队列状态）',
    }
  }
  if (hubSeen && !ov!.offline) {
    return {
      text: 'hub 已注册 · 无进程',
      tone: 'warn',
      title:
        'hub 的课程表里有这门课且在线，但没有任何训练进程推进它——派给它的 job 没有人消费，' +
        '会一直堆在队列里（这份信号在分开的两张表上各自都是「正常」的）',
    }
  }
  return {
    text: '未在训',
    tone: 'off',
    title: lq
      ? STOPPED_TITLE
      : '没有存活的共享 trainer 进程，且训练侧只读视图不可用——这一行只有 hub 侧事实',
  }
}

/** 冲突归类（供筛选/计数用；文案已在状态列里）。 */
export function matrixConflict(
  ov: CourseOverviewRow | null,
  lq: LoopQueueRow | null,
  hubOnline: boolean,
): MatrixConflict {
  const training = rowTraining(ov, lq)
  const hubKnown = ov !== null && hubOnline
  if (training && hubKnown && !ov.hubSeen) return 'hub-unregistered'
  if (!training && hubKnown && ov.hubSeen && !ov.offline) return 'hub-no-process'
  return null
}

// ────────────────────────── 单元格 ──────────────────────────

/** 单侧缺失时的占位单元格（`—` + 说明为什么不知道）。 */
export interface MatrixCell {
  text: string
  title: string
  /** 等宽（url / jid / 数字列）。 */
  mono?: boolean
  /** 需要醒目标出的单元格（如超时未动的离线段）。 */
  warn?: boolean
}

/** 「队列 N · 在飞 N」——hub 侧事实。hub 无应答时是「不知道」，不是 0。
 *
 *  ★ 2026-09-22（离线课列修正）：hub 把课标了**离线**（只收回传、不实时派发 PPO）时，
 *  队列深度不再适用——读面直接说「只收回传」，不摆一个会误读的「队列 0 · 在飞 0」。 */
export function queueCell(ov: CourseOverviewRow | null, hubOnline: boolean): MatrixCell {
  if (ov?.offline)
    return {
      text: '只收回传',
      title:
        'hub 离线模式：不实时派发 PPO，只接收 it 权重/指标回传——队列深度不适用；' +
        '云机整段的段内进度见「段内」列（回传轮次/最近产物）。',
    }
  if (!ov || !hubOnline) return { text: CELL_UNKNOWN, title: HUB_DOWN_TITLE }
  return {
    text: `队列 ${ov.queuePending} · 在飞 ${ov.inflight}`,
    title: `队列深度 ${ov.queuePending}（可领取 job 数）· 在飞 ${ov.inflight}（已发布未回传）`,
  }
}

/** 离线段内进度——**只给离线且已回传过的课**（其余的徽标已经说了「离线/未在训」）。
 *
 *  `nowSec` 由调用方给（不在这里读表）：判据与时刻解耦才能单测，也符合本仓「模拟里不读墙钟」
 *  的同款纪律（这里是呈现层，但确定性单测的价值一样）。 */
export function segmentCell(ov: CourseOverviewRow | null, nowSec: number): MatrixCell | null {
  if (!ov || !ov.offline || ov.offlineRounds === 0) return null
  const stale = ov.offlineLastMtime > 0 && nowSec - ov.offlineLastMtime > OFFLINE_STALE_SEC
  const ago =
    ov.offlineLastMtime > 0 ? fmtRel(ov.offlineLastMtime * 1000, nowSec * 1000) : CELL_UNKNOWN
  return {
    text: `段内 ${ov.offlineRounds} 轮 · 最近 ${ago}`,
    warn: stale,
    title:
      `云机已回传的段内轮次：${ov.offlineRounds} 轮，最新 it${ov.offlineLastIter ?? CELL_UNKNOWN}，` +
      `最近一件产物 ${ago}。这些轮**不在课程账本里**（hub 不跑它们），只有这里看得到。` +
      (stale ? `⚠ 已超过 ${OFFLINE_STALE_SEC / 60} 分钟没新产物——云机可能挂了。` : ''),
  }
}

/** 「在等什么」——训练侧事实。调度器视图不可用时是「不知道」，不是「无待办」。 */
export function waitingCell(lq: LoopQueueRow | null): MatrixCell {
  if (!lq) return { text: CELL_UNKNOWN, title: QUEUE_DOWN_TITLE }
  return {
    text: lq.waiting.text || CELL_UNKNOWN,
    title: waitTitle(lq.waiting.kind),
  }
}

/** 「在等什么」——**离线课**的（★2026-09-22 列修正）：不读本地 13 步表的「推进中/采集中」
 *  等词——那段由云机整段执行，本地只收回传。有回传就说「云机运行中 · 已回传 N 轮」，
 *  一次都还没回传就说「离线（只收回传）」。 */
export function offlineWaitCell(ov: CourseOverviewRow): MatrixCell {
  if (ov.offlineRounds > 0) {
    return {
      text: `云机运行中 · 已回传 ${ov.offlineRounds} 轮`,
      title:
        `云机整段在上跑：已回传的段内轮次 ${ov.offlineRounds} 轮，最新 it${ov.offlineLastIter ?? CELL_UNKNOWN}；` +
        '本地 hub 只收回传、不实时派发 PPO（进度明细与「最近多久没动」见「段内」列）。',
    }
  }
  return {
    text: '离线（只收回传）',
    title:
      'hub 把该课标为离线：云机整段执行，本地只收 it 权重/指标回传；尚未有段内产物回传' +
      '（刚开课 / 云机还没回第一件）。',
  }
}

// ────────────────────────── 合并 ──────────────────────────

export interface CourseMatrixRow {
  course: string
  /** 当前查看的那门课（高亮）。 */
  viewing: boolean
  status: MatrixStatus
  conflict: MatrixConflict
  /** 账本/队列指针（**优先训练侧**：它是「下一轮要跑的 it」，hub 侧那个只读账本尾行）。
   *  ★2026-09-22：**离线课**给的是 `offline`——云机回传的最新 it（不是本地「下一轮」指针）。 */
  iter: number | null
  iterSource: 'queue' | 'ledger' | 'offline' | null
  /** 两侧原始行（`null` = 那一侧没有这门课）。面板要用它们渲染各自的动作与徽章。 */
  ov: CourseOverviewRow | null
  lq: LoopQueueRow | null
  /** 切离线/在线开关能不能给：**hub 在线 ∧ hub 认识这门课**。
   *
   *  不满足时任一侧点下去都是 400（hub 不认识它 / 根本没 hub）——画一个一定失败的按钮
   *  就是假承诺，这不是「只读不禁用」那条：只读是权限边界，这里是能力边界。 */
  canToggleMode: boolean
  /** 控制台意图（`console-state.courseModes`）与 hub 事实**不一致**时的两侧取值。
   *
   *  这不是猜测：两个源都能读到才判（意图存在 ∧ hub 认识这门课），不一致就是「意图没落地」
   *  ——2026-09-23 实测：hub 重启时控制台的回灌跑在 hub 发现这门课之前（POST 400），该课
   *  静默留在 online，面板一路显示「在训 / 切离线」（而操作员以为自己开的是离线课），
   *  直到翻 hub 日志才发现。UI 的职责是把这两个源摆在一起，不是替哪一边编状态。
   *
   *  `null` = 无从判断（没有意图 / hub 不认识它 —— 那时只有一侧事实）。
   *
   *  ★ 带上两侧取值（而不是一个 bool）：渲染层要用它们写清「意图是 X、hub 现在当 Y」，
   *  否则组件就得自己去读那份意图表 —— 又一条知道真相的路径。 */
  modeDrift: { intent: 'online' | 'offline'; hubOffline: boolean } | null
  /** 行内是否给**任务包操作**（导出/导入训练结果）。
   *
   *  判据 = `hub 标了离线 ∨ 控制台意图是离线`（**两个源任一为离线**）。
   *
   *  ★2026-09-23（用户指令）：此前只看 hub 事实（`ov.offline`）——那形成一个死锁：
   *  离线课**需要任务包**才能上云跑，而「导出任务包」键却要等 hub 先接受离线模式才出现；
   *  偏偏回灌失配时（hub 仍当它在线，见 `modeDrift`）那个键正好不见了——最需要它的那一刻。
   *  任务包能力是**课程级**的（与 hub 此刻派不派活正交：包就是给云机用的），故把意图也计入。
   *  两个源都是「离线」时仍然只有一个键（不是两个）。 */
  bundleOps: boolean
  queue: MatrixCell
  waiting: MatrixCell
  segment: MatrixCell | null
  /** 这一点位是否有 BC/RL 种类徽标（BC 行形状与 RL 不同，不标会被读错）。 */
  kind: 'rl' | 'bc'
}

export interface CourseMatrixInput {
  /** hub 侧（`null` = 没读到 / 没请求）。 */
  overview: ParallelOverviewView | null
  /** 训练侧（`null` = 只读视图不可用）。 */
  queue: LoopQueueView | null
  /** 控制台记录的每课派发**意图**（`stateView.courseModeIntents`；缺省 = 旧视图/无意图）。
   *
   *  与 `overview` 里的 hub 事实**分开收**：一个是运维的决定，一个是 hub 此刻的表。
   *  摆在一起才能看出「意图没落地」（`modeDrift`）——不在这里替任何一侧编事实。 */
  modeIntents?: Record<string, 'online' | 'offline'> | null
  viewing: string
  /** 判定「段内多久没动」的当下时刻（epoch 秒）——调用方给，便于单测。 */
  nowSec: number
}

/** 意图 vs hub 事实：不一致 = 「意图没落地」（`null` = 无从判断，只一侧有事实）。
 *
 *  为什么是 `null` 而不是 `false`：没有意图（从没点过切离线 / 历史课）或 hub 不认识这门课时，
 *  「一致」根本没有内容——把「不知道」画成「没问题」正是这一整类事故的成因。
 */
export function modeDriftOf(
  course: string,
  ov: CourseOverviewRow | null,
  hubOnline: boolean,
  intents?: Record<string, 'online' | 'offline'> | null,
): { intent: 'online' | 'offline'; hubOffline: boolean } | null {
  const intent = intents?.[course]
  if (!intent) return null
  if (!ov || !hubOnline || !ov.hubSeen) return null
  if (ov.offline === (intent === 'offline')) return null
  return { intent, hubOffline: ov.offline }
}

/** 两侧 outer join（顺序：先 hub 侧给出的序，再补训练侧独有的课 —— 稳定且「在训的在前」）。 */
export function mergeCourseRows(input: CourseMatrixInput): CourseMatrixRow[] {
  const ovRows = input.overview?.rows ?? []
  const lqRows = input.queue?.rows ?? []
  const lqByCourse = new Map(lqRows.map((r) => [r.course, r]))
  const hubOnline = input.overview?.hubOnline ?? false

  const order: string[] = []
  const seen = new Set<string>()
  for (const r of ovRows) {
    if (seen.has(r.course)) continue
    seen.add(r.course)
    order.push(r.course)
  }
  for (const r of lqRows) {
    if (seen.has(r.course)) continue
    seen.add(r.course)
    order.push(r.course)
  }

  const ovByCourse = new Map(ovRows.map((r) => [r.course, r]))
  return order.map((course) => {
    const ov = ovByCourse.get(course) ?? null
    const lq = lqByCourse.get(course) ?? null
    const fromQueue = lq ? lq.it : null
    const fromLedger = ov ? ov.iter : null
    // ★ 2026-09-22（离线课列修正）：离线课走「任务包+回传」维度——iter 列读**云机回传的最新
    // it**（`offlineLastIter`），不是本地「下一轮要跑」的队列指针（那对本机读面是错的）。
    const offlineIt = ov?.offline ? (ov.offlineLastIter ?? null) : null
    const iter = offlineIt !== null ? offlineIt : fromQueue !== null ? fromQueue : fromLedger
    return {
      course,
      viewing: course === input.viewing,
      status: matrixStatus(ov, lq, hubOnline),
      conflict: matrixConflict(ov, lq, hubOnline),
      iter,
      iterSource:
        offlineIt !== null
          ? 'offline'
          : fromQueue !== null
            ? 'queue'
            : fromLedger !== null
              ? 'ledger'
              : null,
      ov,
      lq,
      canToggleMode: hubOnline && (ov?.hubSeen ?? false),
      modeDrift: modeDriftOf(course, ov, hubOnline, input.modeIntents),
      bundleOps: (ov?.offline ?? false) || input.modeIntents?.[course] === 'offline',
      queue: queueCell(ov, hubOnline),
      waiting: ov?.offline ? offlineWaitCell(ov) : waitingCell(lq),
      segment: segmentCell(ov, input.nowSec),
      kind: lq?.kind ?? 'rl',
    }
  })
}

// ────────────────────────── 表头元信息 ──────────────────────────

/** 表头一行：两侧各自的机群级读数（不是每课事实）。 */
export interface MatrixMeta {
  text: string
  title: string
  tone?: MatrixTone
}

/** hub 侧 + 训练侧的机群级读数，拼成表头。任一侧缺失时**明确说哪一侧缺**，不静默丢一半。 */
export function matrixMeta(input: {
  overview: ParallelOverviewView | null
  queue: LoopQueueView | null
}): MatrixMeta[] {
  const out: MatrixMeta[] = []
  const ov = input.overview
  if (!ov) {
    out.push({ text: 'hub 视图未读', title: HUB_DOWN_TITLE, tone: 'warn' })
  } else if (!ov.hubOnline) {
    out.push({ text: 'hub 无应答', title: HUB_DOWN_TITLE, tone: 'warn' })
  } else {
    out.push({
      text: `hub ${ov.hubUrl ? shortHost(ov.hubUrl) : ''}`.trim(),
      title: `队列观测源：${ov.hubUrl}/admin/queue`,
      tone: 'ok',
    })
    out.push({
      text: `在派发 ${ov.activeCourses} / worker ${ov.activeWorkers}`,
      title: '在实时派发的课程数 / 窗口内活跃 worker 数',
    })
    if (ov.recentDispatch)
      out.push({
        text: `最近派发 ${ov.recentDispatch}`,
        title: 'hub 轮转游标：上一份 job 派给了这门课（下一份从它的下一门开始扫）',
      })
    if (ov.halt)
      out.push({
        text: '停机中',
        title: '已向云机下发停机达令（任务照常分发，云机停不掉就继续干活）',
        tone: 'warn',
      })
  }
  const lq = input.queue
  if (!lq) {
    out.push({ text: '调度器视图未读', title: QUEUE_DOWN_TITLE, tone: 'warn' })
  } else {
    out.push({
      text: `在训 ${lq.trainingCount}/${lq.rows.length}`,
      title: '共享 trainer 在跑（调度器活着）且这门课未收官；总数 = 账本/课程目录可发现的课程数',
      tone: 'ok',
    })
    const waiting = lq.rows.filter((r) => r.waiting.kind === 'inflight' && r.training).length
    if (waiting > 0)
      out.push({
        text: `${waiting} 课等回传`,
        title: '正在等远端 job 回传的在训课程数（结果在 GPU worker / 云机上）',
        tone: 'warn',
      })
    if (lq.blockedCourses.length > 0)
      out.push({
        text: `排队等资源：${lq.blockedCourses.join('、')}`,
        title: '本机重资源（PPO / eval）池已满：这些课在排队等票（单进程调度器的正常态）',
        tone: 'warn',
      })
    const pools = Object.keys(lq.pools).sort()
    if (pools.length > 0)
      out.push({
        text: pools.map((n) => `${n} ${lq.pools[n]!.held}/${lq.pools[n]!.capacity}`).join(' · '),
        title: '本机重资源池占用/容量（任一时刻可持有票数）',
      })
    if (lq.error) out.push({ text: '上一拍读失败（显示缓存）', title: lq.error, tone: 'warn' })
  }
  return out
}

/** hub 基址的短展示（只留主机名/端口，去掉协议，表头不占宽）。 */
function shortHost(url: string): string {
  return url.replace(/^https?:\/\//, '').replace(/\/+$/, '')
}

/** 页脚：口径来源 + 排队时的一句人话（等票不是卡死）。 */
export function matrixFoot(input: {
  queue: LoopQueueView | null
  rows: CourseMatrixRow[]
}): string {
  const lq = input.queue
  if (lq && lq.blockedCourses.length > 0) {
    return '本机重资源跨课排队（容量 1）：等票的课不是卡死，持票课一步跑完即自动继续。'
  }
  const blocked = input.rows.find((r) => r.lq && r.lq.inflight.length > 0 && r.lq.training)
  if (blocked?.lq) {
    const job = blocked.lq.inflight[0]!
    const who = job.jid
      ? `（jid=${job.jid.slice(0, 12)}${job.dispatch ? ` via ${job.dispatch}` : ''}）`
      : ''
    return `${blocked.course} 正在等 ${job.phase}@${job.round}${who} 回传；其余课照常推进（等外部不占执行权）。`
  }
  if (!lq) return `口径：训练侧只读视图不可用；本表只有 hub 侧事实（${input.rows.length} 课）。`
  return `口径：训练侧只读视图 run_rl_cluster.py --json（${input.rows.length} 课）+ registry 在训事实；${lq.trainingCount}/${lq.rows.length} 课有存活进程。`
}

/** 「在等什么」单元格的修饰类（供面板上色）。 */
export function waitingClass(lq: LoopQueueRow | null): string {
  return lq ? waitCls(lq.waiting.kind) : ''
}

/** 暂停开关的（文案 + 悬停 + 事实徽标）——两侧动作各自归属，见 `docs/dashboard-redesign.md` §3.3 O4。
 *
 *  开关改的是**意图文件**，能不能生效由训练进程决定 ⇒ 旁边再挂一个**事实**徽标，
 *  诚实区分「已暂停」与「待生效」（只看意图会骗人，只看事实则点完没反馈）。 */
export function pauseOp(lq: LoopQueueRow | null): {
  label: string
  title: string
  badge: { text: string; tone: NonNullable<RowBadge['tone']> } | null
} | null {
  if (!lq) return null
  const b = pauseBadge(lq)
  return {
    label: pauseLabel(lq),
    title: pauseTitle(lq),
    badge: b ? { text: b.text, tone: matrixBadgeTone(b.tone) } : null,
  }
}
