/** course-matrix.ts — 课程矩阵：把「并行课程总览」（hub 侧）与「训练调度器」（训练侧）
 *  按课程名 **outer join** 成一张表（问题 C5）。纯函数，无 IO、无 node:、无 Bun。
 *
 *  ## 为什么必须合并
 *
 *  这两张表回答的是**同一个问题**——「这门课现在怎么样」——却各写了一半，而且两半**互相看不见**：
 *
 *  - 「并行课程总览」知道 hub 侧事实：这门课在不在 hub 课程表里、**谁在接管**、队列积压多少、
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

import type { LoopComplete } from './console-types'
import type { CourseOverviewRow, ParallelOverviewView, ReadStaleView } from './course-overview'
import { courseStatus, type CourseStatus } from './course-status'
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

/** 接管段内产物「多久没动了」的红线：超过它就当可疑（自主盘挂掉与还在跑的唯一区分）。
 *  自主盘那份在云机上跑（一轮可能十几分钟），30 分钟仍属正常长轮，故定 1h。
 *  ★M4：真正的「掉线」判据是 hub 的 `hold.state`（900s 无**进度信号**）——本常量只是
 *  这张表上「最近一件产物多久没动」的展示阈值，两者不得互相替代。 */
export const OFFLINE_STALE_SEC = 3600

// ────────────────────────── 状态判定 ──────────────────────────

/** 状态的档位（多对一映射到 `StatusDot` 的四档，映射的**理由**写在 `matrixStatus` 里）。
 *
 *  `info` 是四档之外的第五种**语义**：`off`（未在训）表示「没在干活」，而 `info` 表示
 *  「在干活、只是归云机管（接管中）」——两者都不该涂成黄/红，但混成同一个灰点会把
 *  「云机执行中」读成「停着」。 */
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
  '训练侧只读视图 trainer/run_rl_cluster.py --json 不可用——指针与「在等什么」不可知（不是「无待办」）'

/** 单侧缺失时单元格里的占位符。**不是 0**：0 是「确定没有」，`—` 是「不知道」。 */
export const CELL_UNKNOWN = '—'

/** **「上屏」判据**（本模块唯一出处）：**任一侧说在训就算**（`lq.training ∨ ov.training`）
 *
 *  ★P1-10（2026-10-05）：两个事实**定义不同**——`ov.training` 是开课标记（会话级快照），
 *  `lq.training` 是进程事实（调度器存活 ∧ 该课未收官）。它们不一致的时刻（已收官、调度器
 *  没跑、hub 单侧注册）恰恰是合并前最容易漏掉的信号 ⇒ 上屏用 **OR**：任一侧在训的行都
 *  不隐藏（冲突行正是这张表要盯的）。
 *
 *  ⚠ 它只是**上屏筛**，不是状态词的判据：词由 `courseStatus` 单点派生（同样的两半，
 *  按语义优先级给一个答案）——比如「hub 说在训、训练侧没进程」的行上屏但状态词是
 *  `hub 已注册 · 无进程`（warn），不会跟着 `ov.training` 谎报「在训」。 */
export function rowTraining(ov: CourseOverviewRow | null, lq: LoopQueueRow | null): boolean {
  return (lq?.training ?? false) || (ov?.training ?? false)
}

/** **状态词的进程事实**：有训练侧行就信它（`lq.training`）；行缺失才退 hub 的开课标记。
 *  与 `rowTraining`（上屏 OR）刻意分开——上屏要「两边任一」，词要「一个答案」。 */
export function rowProcessTraining(ov: CourseOverviewRow | null, lq: LoopQueueRow | null): boolean {
  return lq ? lq.training : (ov?.training ?? false)
}

/** 行级包装：面板按它筛「哪些行上屏」（课程区只列在训课程）。
 *
 *  ★ 与 `matrixStatus` 用**同一个**判据：状态列说「在训」而这一行没上屏（或反过来）就是 bug。 */
export function isTrainingRow(r: CourseMatrixRow): boolean {
  return rowTraining(r.ov, r.lq)
}

/**
 * 一行 → 状态。**判据不再在这里拼**（P1-10）：交给唯一派生 `courseStatus`（`course-status.ts`），
 * 本函数只把它说的语义**说成矩阵的词**（pill 说成自己的细词）——两边同源，不可能互相矛盾。
 *
 * 矩阵词的顺序即「哪条信息最该先说」：
 * 1. 在训但 hub 没注册（warn）——算力在烧，job 永远不派发。
 * 2. 确定性状态（暂停/收官/中止）——此前矩阵看不见它们，与 pill 矛盾（R4-a）。
 * 3. **接管（hold）**（info/warn）——★M4 取代了旧的「hub 标了离线」：
 *    live = 归云机管（info）；stale = 接管掉线（warn，已自愈但要人知道）。
 * 4. 在训（ok）。
 * 5. hub 在线且注册了它，却没有进程（warn）——job 会堆起来。
 * 6. 其余：未在训（off）。
 *
 * ⚠ 第 1、5 档**必须**以 `hubOnline` 为前提：hub 无应答时 `hubSeen` 恒为 false（队列整个读不到），
 * 拿它当「hub 不认这门课」是**假诊断**——面板表头明明写着「hub 无应答」，行里却叫人去用
 * `--course` 重启 hub。合并前的总览卡就犯了这个错。
 */
export function matrixStatus(
  ov: CourseOverviewRow | null,
  lq: LoopQueueRow | null,
  /** hub 此刻在不在应答——**每行事实之外的机群级事实**，故由调用方显式给：
   *  `hubSeen === false` 只有在 hub 在线时才读作「hub 不认这门课」，否则是「不知道」。 */
  hubOnline: boolean,
  /** ★P1-11：读面新鲜度标注（读失败保旧值时不改词）；缺省 = 本拍读成功。 */
  opts?: {
    overviewStale?: ReadStaleView | null
    queueStale?: ReadStaleView | null
    registeredWorkers?: string[] | null
    /** ★2026-10-06：账本尾行 `run_complete` 的停车态（`stateView.loopCompletes[课]`）——
     *  终态词与 pill 同源（同屏两侧不得再互相矛盾）。 */
    loopComplete?: LoopComplete | null
  },
): MatrixStatus {
  const st = courseStatus({
    course: '',
    lq,
    ov,
    hubOnline,
    // 有队列行就信它自己的进程事实（`lq.training` = 调度器存活 ∧ 未收官）；
    // 没有行时退 hub 的粗档（见 `rowProcessTraining`）。
    trainerRunning: rowProcessTraining(ov, lq),
    registeredWorkers: opts?.registeredWorkers,
    overviewStale: opts?.overviewStale ?? null,
    queueStale: opts?.queueStale ?? null,
    loopComplete: opts?.loopComplete ?? null,
  })
  return matrixWord(st)
}

/** 语义（`courseStatus` 的输出）→ 矩阵词表（渲染器只决定词怎么说）。 */
function matrixWord(st: CourseStatus): MatrixStatus {
  // ① hub 冲突档前置：算力在烧而 job 永不派发 / job 没人消费——矩阵独有的升级信号。
  if (st.conflict === 'hub-unregistered') {
    return {
      text: '在训 · hub 未注册',
      tone: 'warn',
      title:
        'hub 的课程表里没有这门课——它的 PPO job 永远不会被派发（rollout 白跑）。' +
        '以 `--course <课>` 重启 hub，或把 hub 的课程表补上这门课',
    }
  }
  // ② 确定性状态：与 pill 同词（同屏两侧不得互相矛盾，R4-a）。
  if (st.kind === 'paused') return { text: '已暂停', tone: 'info', title: st.title }
  if (st.kind === 'done') return { text: '已收官', tone: 'off', title: st.title }
  if (st.kind === 'aborted') return { text: '已中止', tone: 'err', title: st.title }
  // ★ 2026-10-05（plan/course-startup-recover §3.3）：配置不可开课 ⇒ 红（与 pill 同词）。
  if (st.kind === 'blocked') return { text: '起不来', tone: 'err', title: st.title }
  // ③ **接管（hold）**：★M4 词表。live = 归云机管（info，不是故障）；
  //    stale = 接管掉线（warn：它已自愈，但「刚才丢了 15 分钟」这件事要人看见）。
  if (st.kind === 'autonomous-running' || st.kind === 'autonomous-waiting') {
    return { text: '接管中（云机）', tone: 'info', title: st.title }
  }
  if (st.kind === 'autonomous-stale') return { text: '接管掉线', tone: 'warn', title: st.title }
  if (st.kind === 'held-wait') return { text: '接管中（云机）', tone: 'info', title: st.title }
  // ④ hub 注册了它却没进程：job 会堆起来（warn）。
  if (st.conflict === 'hub-no-process' || st.kind === 'hub-no-process') {
    return {
      text: 'hub 已注册 · 无进程',
      tone: 'warn',
      title: st.title,
    }
  }
  // ⑤ 在训：pill 的细词（采集中/等回传/卡住/…）在矩阵合并成粗词，但**语义同源**。
  if (st.kind !== 'not-training' && st.kind !== 'waiting-process' && st.kind !== 'unknown') {
    return { text: '在训', tone: 'ok', title: st.title }
  }
  return {
    text: '未在训',
    tone: 'off',
    title: st.kind === 'waiting-process' || st.kind === 'not-training' ? st.title : STOPPED_TITLE,
  }
}

/** 冲突归类（供筛选/计数用；文案已在状态列里）。
 *
 *  ★P1-10：与状态列**同一个出处**（`courseStatus` 的 `conflict` 档）——从前这里是
 *  `matrixStatus` 旁边的一份平行实现，两个判据只要漂开就会「状态列说已注册无进程、
 *  冲突列说 hub 未注册」这种同屏自相矛盾。 */
export function matrixConflict(
  ov: CourseOverviewRow | null,
  lq: LoopQueueRow | null,
  hubOnline: boolean,
  /** ★2026-10-06：停车态（同 `matrixStatus`）——终态不得被 hub 冲突档升成「在训 · hub 未注册」。 */
  loopComplete: LoopComplete | null = null,
): MatrixConflict {
  return courseStatus({
    course: '',
    lq,
    ov,
    hubOnline,
    trainerRunning: rowProcessTraining(ov, lq),
    loopComplete,
  }).conflict
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
 *  ★M4（接管列修正）：这门课**正被接管**（hold live）时，队列深度不再适用——该课对协作
 *  worker 是不可派的（派发闸看 hold），摆一个「队列 0 · 在飞 0」会被读成「没人要跑」。
 *  `stale` 的 hold 不适用这一档：派发已经恢复，队列列就是真相。 */
export function queueCell(ov: CourseOverviewRow | null, hubOnline: boolean): MatrixCell {
  if (ov?.hold?.state === 'live')
    return {
      text: '接管中·不派发',
      title:
        '该课正被自主 worker 接管：PPO 派发被 hold 闸压住（在队任务不撤、也不派给协作盘）——' +
        '队列深度不适用；段内进度见「接管」列（回传轮次/最近产物）。',
    }
  if (!ov || !hubOnline) return { text: CELL_UNKNOWN, title: HUB_DOWN_TITLE }
  return {
    text: `队列 ${ov.queuePending} · 在飞 ${ov.inflight}`,
    title: `队列深度 ${ov.queuePending}（可领取 job 数）· 在飞 ${ov.inflight}（已发布未回传）`,
  }
}

/** **接管列**（★M4：取代旧的「段内」列）——holder / 最近回传龄 / 段内轮数。
 *
 *  为什么合并：旧「段内」列只在 `offline` 模式下出现，而今天「这堂课归谁」全靠 hold 回答——
 *  一行得同时说清「谁在跑、多久没回传、跑到哪了」。**没有接管**时给 `null`（不画这列），
 *  而不是一个空的「—」：本机跑的课不需要这一列（它的进度在「在等什么」与迭代列里）。
 *
 *  `nowSec` 由调用方给（不在这里读表）：判据与时刻解耦才能单测，也符合本仓「模拟里不读墙钟」
 *  的同款纪律（这里是呈现层，但确定性单测的价值一样）。 */
export function holdCell(ov: CourseOverviewRow | null, nowSec: number): MatrixCell | null {
  const hold = ov?.hold
  if (!ov || !hold) return null
  const ago =
    hold.lastProgressAt > 0 ? fmtRel(hold.lastProgressAt * 1000, nowSec * 1000) : CELL_UNKNOWN
  const staleArtifact = ov.offlineLastMtime > 0 && nowSec - ov.offlineLastMtime > OFFLINE_STALE_SEC
  const who = hold.workerId || '未登记持有者'
  if (hold.state === 'stale') {
    return {
      text: `接管掉线 · ${who}`,
      warn: true,
      title:
        `持有人「${who}」的进度已静默超阈值（hub ` +
        `hold.state=stale）——**协作派发与本机采样已自动恢复**，新自主盘可直接 claim 接管。` +
        `已回传段内 ${ov.offlineRounds} 轮（最新 it${ov.offlineLastIter ?? CELL_UNKNOWN}）。` +
        '到控制台可「强制解除接管」（立墓碑，现场看得见）。',
    }
  }
  return {
    text: `${who} · 最近 ${ago}`,
    warn: staleArtifact,
    title:
      `${who} 接管中（hold live：压住协作派发 + 本机不跑这门课），最近一次**进度**信号 ${ago} 前。` +
      `已回传段内 ${ov.offlineRounds} 轮，最新 it${ov.offlineLastIter ?? CELL_UNKNOWN}；` +
      `最近一件产物 ${
        ov.offlineLastMtime > 0 ? fmtRel(ov.offlineLastMtime * 1000, nowSec * 1000) : CELL_UNKNOWN
      }。` +
      '活性只认进度信号（心跳不算）；15 分钟无进度自动解除。' +
      (staleArtifact ? `⚠ 已超过 ${OFFLINE_STALE_SEC / 60} 分钟没新产物——云机可能挂了。` : ''),
  }
}

/** 有接管吗（上屏/占列用；`null` = 旧 hub 没这一列）。 */
export function hasHold(ov: CourseOverviewRow | null): boolean {
  return !!ov?.hold
}

/** 「在等什么」——训练侧事实。调度器视图不可用时是「不知道」，不是「无待办」。 */
export function waitingCell(lq: LoopQueueRow | null): MatrixCell {
  if (!lq) return { text: CELL_UNKNOWN, title: QUEUE_DOWN_TITLE }
  return {
    text: lq.waiting.text || CELL_UNKNOWN,
    title: waitTitle(lq.waiting.kind),
  }
}

/** 「在等什么」——**被接管的课**的（★M4）：不读本地 13 步表的「推进中/采集中」等词——
 *  那段由自主 worker 整段执行，本地只收回传。有回传就说「云机运行中 · 已回传 N 轮」，
 *  一次都还没回传就说「等待接管」。 */
export function holdWaitCell(ov: CourseOverviewRow): MatrixCell {
  if (ov.hold?.state === 'stale') {
    return {
      text: '接管掉线 · 已恢复协作',
      title:
        '接管已判掉线（进度静默超阈）：该课已回到协作派发 + 本机采样——这一行接下来会按' +
        '训练侧的「在等什么」推进；要立刻清掉墓碑可点「强制解除接管」。',
    }
  }
  if (ov.offlineRounds > 0) {
    return {
      text: `云机运行中 · 已回传 ${ov.offlineRounds} 轮`,
      title:
        `自主 worker 整段在上跑：已回传的段内轮次 ${ov.offlineRounds} 轮，最新 it${ov.offlineLastIter ?? CELL_UNKNOWN}；` +
        '本机不跑这门课（held 等待），PPO 派发也被 hold 闸压住（进度明细见「接管」列）。',
    }
  }
  return {
    text: '等待接管',
    title:
      '该课被接管但还没有任何段内产物回传：云机可能刚领走任务包、或还在跑第一轮' +
      '（本机不跑这门课，PPO 派发被 hold 闸压住）。',
  }
}

// ────────────────────────── 合并 ──────────────────────────

export interface CourseMatrixRow {
  course: string
  /** 当前查看的那门课（高亮）。 */
  viewing: boolean
  status: MatrixStatus
  conflict: MatrixConflict /** 账本/队列指针（**优先训练侧**：它是「下一轮要跑的 it」，hub 侧那个只读账本尾行）。
   * ★M4：**被接管的课**给的是 `hold`——云机回传的最新 it；这一段还没回传 ⇒ `null`
   * （**不可知**，不是本地「下一轮」指针——本机这一段不跑这门课）。 */
  iter: number | null
  iterSource: 'queue' | 'ledger' | 'hold' | null
  /** 两侧原始行（`null` = 那一侧没有这门课）。面板要用它们渲染各自的动作与徽章。 */
  ov: CourseOverviewRow | null
  lq: LoopQueueRow | null
  /** **强制解除接管**能不能给：**hub 在线 ∧ hub 认识这门课 ∧ 确实有接管或导包软态**。
   *
   *  不满足时任一侧点下去都是 404/409（hub 不认识它 / 根本没 hub / 本来就没接管）——画一个
   *  一定失败的按钮就是假承诺，这不是「只读不禁用」那条：只读是权限边界，这里是能力边界。
   *  ★M4：它取代了旧的三颗模式钮（切离线/切换成在线/交还自动）。 */
  canReleaseHold: boolean
  /** 行内是否给**任务包操作**（导出/导入训练结果）。
   *
   *  判据 = **这门课在训**（任一侧说在训，`rowTraining`）。
   *
   *  ★M4：旧判据是「hub 离线 ∨ 意图离线」——两个源都随模式语义退役。今天的事实是：
   *  **任何一门在训课都可能被自主 worker 领走**（claim 遇缺包时 hub 会请控制台导包），
   *  所以「导出任务包」对每一门在训课都是合法动作（与 hub 此刻派不派活正交：包就是给
   *  云机用的）。停课的课不画（它连认课标记都没了，云机领不走）。 */
  bundleOps: boolean
  queue: MatrixCell
  waiting: MatrixCell
  /** **接管列**（★M4：取代旧「段内」列）：holder / 最近回传龄 / 段内轮数；
   *  `null` = 这门课没有接管（不画锁列，而不是画一个空的「—」）。 */
  hold: MatrixCell | null
  /** 这一点位是否有 BC/RL 种类徽标（BC 行形状与 RL 不同，不标会被读错）。 */
  kind: 'rl' | 'bc'
  /** **接管徽标**（★M4：取代旧的权威三态徽标）：接管掉线（stale）/ 导包中（pending_export）。
   *  `null` = 没有这两件事（live 接管由状态列与接管列说，不重复画徽标）。 */
  holdBadge: MatrixBadge | null
  /** **离线租约徽标**（P1-6）：`stale`（静默超阈、新盘可直接接管）/ `revoked`（已撤租墓碑）。
   *  `null` = 没有租约 / 旧 hub。 */
  leaseBadge: MatrixBadge | null
}

/** 行级徽标（词 + 语义档 + 悬停全因）。 */
export interface MatrixBadge {
  text: string
  tone: NonNullable<RowBadge['tone']>
  title: string
}

/** **接管徽标**（★M4）：只画需要处置/需要知道的两态——
 *  · `stale`（接管掉线）：状态列已经说了「接管掉线」，但徽标把**「已恢复协作」**放在同一行
 *    的显眼处（操作员最常问的就是「本机到底跑不跑这门课」）；
 *  · `pending_export`（导包中）：**不占闸**的软态——不画它，操作员会把「刚认领还没包」
 *    误读成「没人管」。
 *
 *  live 的接管不另画徽标：状态列说「接管中（云机）」、接管列说 holder——同一件事不摆三遍。 */
export function holdBadgeOf(ov: CourseOverviewRow | null): MatrixBadge | null {
  if (!ov) return null
  if (ov.hold?.state === 'stale') {
    return {
      text: '已恢复协作',
      tone: 'y',
      title:
        '接管掉线（进度静默超阈）：该课已自动回到协作派发 + 本机采样（在队任务照常可领）。' +
        '要立刻清掉墓碑可点「强制解除接管」。',
    }
  }
  if (ov.pendingExport && !ov.hold) {
    return {
      text: '导包中',
      tone: 'a',
      title:
        `有自主 worker 已认领该课（触发方 ${ov.pendingExport.by || '未知'}）——控制台正在导出` +
        '任务包。**包到手前不建接管**：本机照跑、协作照派（软态不占闸）。',
    }
  }
  return null
}

/** 离线租约 → 徽标（P1-6）：只给两个需要处置的档（stale/revoked）；新鲜租约不画（队列格已说）。 */
export function leaseBadgeOf(ov: CourseOverviewRow | null): MatrixBadge | null {
  const lease = ov?.lease ?? null
  if (!lease) return null
  if (lease.revoked) {
    return {
      text: '已撤租',
      tone: 'y',
      title:
        `租约已立墓碑（切在线 / 交还自动的吊销）：旧 worker「${lease.workerId || '?'}」` +
        '下次心跳会收 409 revoked、新 claim 可直接覆盖它；本条目保留只为排障',
    }
  }
  if (lease.stale) {
    return {
      text: '可接管',
      tone: 'y',
      title:
        `持有人「${lease.workerId || '?'}」已连续静默 ${Math.round(lease.silentSec)}s（超阈值）` +
        '——新盘**不必等 TTL**可直接 claim 接管（自动回收，不是故障）',
    }
  }
  return null
}

export interface CourseMatrixInput {
  /** hub 侧（`null` = 没读到 / 没请求）。 */
  overview: ParallelOverviewView | null
  /** 训练侧（`null` = 只读视图不可用）。 */
  queue: LoopQueueView | null
  /** ★2026-10-06：逐课停车态（`stateView.loopCompletes`）——收官终态进状态列，
   *  与 pill 同一派生（缺省 = 旧视图/没有停车态，行为与从前逐字节相同）。 */
  loopCompletes?: Record<string, LoopComplete> | null
  viewing: string
  /** 判定「段内多久没动」的当下时刻（epoch 秒）——调用方给，便于单测。 */
  nowSec: number
}

// ★M4：三源漂移派生（逐课 rollout 源入参）已删——它比的是「控制台意图 vs hub 模式 vs
//  rl-config 的 `rollout_src=run`」三个源，而这三个源头全没了（课程不再有模式；`run` 已退役）。
// 今天的对应物是**接管（hold）**：它只有一个真源（hub），不存在「三个源没对齐」这种形状。

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
    const loopComplete = input.loopCompletes?.[course] ?? null
    const fromQueue = lq ? lq.it : null
    const fromLedger = ov ? ov.iter : null
    // ★M4（接管列修正）：被接管的课走「任务包+回传」维度——iter 列**只**读云机回传的最新 it
    // （`offlineLastIter`）；还没回传时是**不可知**（`null`），不拿本地「下一轮」指针充数
    // （这一段本机压根不跑这门课，那个数字对读面是错的）。没接管的课照旧：队列指针 → 账本尾行。
    const holdIt = ov?.hold ? (ov.offlineLastIter ?? null) : null
    const iter = ov?.hold ? holdIt : fromQueue !== null ? fromQueue : fromLedger
    const iterSource: CourseMatrixRow['iterSource'] = ov?.hold
      ? holdIt !== null
        ? 'hold'
        : null
      : fromQueue !== null
        ? 'queue'
        : fromLedger !== null
          ? 'ledger'
          : null
    return {
      course,
      viewing: course === input.viewing,
      status: matrixStatus(ov, lq, hubOnline, { loopComplete }),
      conflict: matrixConflict(ov, lq, hubOnline, loopComplete),
      iter,
      iterSource,
      ov,
      lq,
      canReleaseHold: hubOnline && (ov?.hubSeen ?? false) && !!ov?.hold,
      bundleOps: rowTraining(ov, lq),
      holdBadge: holdBadgeOf(ov),
      leaseBadge: leaseBadgeOf(ov),
      queue: queueCell(ov, hubOnline),
      waiting: ov?.hold
        ? ov.hold.state === 'stale'
          ? waitingCell(lq)
          : holdWaitCell(ov)
        : waitingCell(lq),
      hold: holdCell(ov, input.nowSec),
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
    out.push({
      text: 'hub 无应答',
      // 读面在这里**没有旧值可显示**（url=null）——但如果是连续失败超窗，把原因带上。
      title: ov.stale ? `${HUB_DOWN_TITLE}。最近一次失败：${ov.stale.reason}` : HUB_DOWN_TITLE,
      tone: 'warn',
    })
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
    // ★P1-11（R4-e）：读失败**不再清空旧行**（last-known-good）——所以「显示缓存」现在是真的；
    //   无旧值可保（冷启动失败 / 超窗）时改说「未知」，不拿一个空表冒充缓存。
    if (lq.error) {
      out.push(
        lq.stale
          ? {
              text: '上一拍读失败（显示缓存）',
              title: `${lq.error}（连续失败自 ${new Date(lq.stale.since).toLocaleTimeString()} 起）`,
              tone: 'warn',
            }
          : { text: '调度器视图未知（读面失败）', title: lq.error, tone: 'warn' },
      )
    }
  }
  // hub 读面新鲜度（P1-11）：探针失败但**还在显示旧值**时点名——不要把它当成「hub 不在应答」
  // （那是维护旧值的同一条事实，只是旧了一拍）；`hubOnline=false` 时上面那句已说清。
  if (ov?.stale && ov.hubOnline) {
    out.push({
      text: 'hub 上一拍读失败（显示缓存）',
      title: `${ov.stale.reason}（连续失败自 ${new Date(ov.stale.since).toLocaleTimeString()} 起）`,
      tone: 'warn',
    })
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
  return `口径：训练侧只读视图 trainer/run_rl_cluster.py --json（${input.rows.length} 课）+ registry 在训事实；${lq.trainingCount}/${lq.rows.length} 课有存活进程。`
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
