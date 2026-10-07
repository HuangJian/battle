/** course-status.ts — 首页课程区的**单一派生**（plan/offline-online-status-switch §3.10 / P1-10）。
 *
 *  为什么要有它（R4-a…R4-g，2026-10-04 三轮补审）：同一门课的状态词此前由 pill
 *  （`loop-queue.ts::coursePills`）与矩阵状态列（`course-matrix.ts::matrixStatus`）**各拼一份**，
 *  两条优先级表不同 ⇒ 同屏两个 widget 可以互相矛盾（离线接管的课 pill「已收官」而矩阵「在训」、
 *  暂停课 pill「已暂停」而矩阵仍「在训」）。本模块把「这门课现在是什么状态」收成唯一一个
 *  派生函数；渲染器只决定「词怎么说」（pill 用自己的细词，矩阵映射到粗词），不决定「是什么」。
 *
 *  事实来源与纪律：
 *    · **接管（hold）不在这里猜**：hub 的 `/admin/queue` 每课行带 `hold`（★M1b），
 *      dashboard 只读（`CourseOverviewRow.hold`）——活性（进度）由 hub 判（`hold_state`）；
 *    · **未知 ≠ 否定**：hub 探针失败 / 训练侧空行都是「不知道」，读面按 R4-g 标注，
 *      不得渲染成「未在训 / hub 不认」这类肯定判断（那是读面自己的缺陷）；
 *    · **滞回**：词只随派生值变化；来源可用性变化（读面失败/恢复）只改 `stale` 标注、
 *      不改词——读失败时上一拍事实仍在（P1-11 的 last-known-good），陈旧窗口见 `stale`。
 *
 *  本模块只放类型与纯函数（无 IO、无 node:、无 Bun）。
 */

import type { LoopComplete } from './console-types'
import type { CourseOverviewRow, ReadStaleView } from './course-overview'
import type { CoursePillTone, LoopQueueRow } from './loop-queue'

/** 「卡住」的展示阈值（秒）。= hub `CLAIM_TTL_SEC`：连一个租约周期都走完了还没回传，
 *  与稳态 wall（58–73s）差 ≥4×。**纯展示层常量**——不得被 hub / 训练侧 import。 */
export const PILL_STUCK_SEC = 300

/** 收官停车态的**续跑指引**（与告警坞 `alerts.ts::loopCompleteAlerts` 同句）。
 *
 *  ★2026-10-06：同一条盘上事实（账本尾行 `run_complete`）在两个 widget 上说话，文案只能一份
 *  ——否则「pill 说改大 iters、告警坞说别的」又是同屏两种说法。 */
export const PARKED_RESUME_ADVICE =
  '本地已停止采集，云机已停机省配额，进程停车等待重启。改大 iters 后经「停止→启动」继续。'

/** 「有活、**没人在飞**」这一类的**唯一词**（pill 与告警坞共用；2026-10-02 口径对齐，plan §6）：
 *  盘上的 `detectPpoQueueStall`（≥5min 无认领的红条）说的就是**同一件事**——两处不得各起
 *  一个名字。分工是「pill 说状态（立即）、告警坞说升级（超时）」，词只能一份。 */
export const NO_TAKER_LABEL = '排队·无人取'

/** 「有在飞、但龄超阈」这一类。pill 专属：告警坞结构上只看得见**无人认领**的 job
 *  （盘上有 `claimed` 标记即跳过，见 `ppo-queue.ts` 头注）——告警坞的区分句引用这个词，
 *  防两类被读成一类（有持有者卡死 ≠ 没人要跑：处置方向相反）。 */
export const STUCK_LABEL = '卡住'

/** 龄 → 展示串：`<60s` 不显示（`''`）；`<60m` 用 `Nm`；否则 `Nh`（plan §3.2）。 */
export function fmtAge(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec) || sec < 60) return ''
  if (sec < 3600) return `${Math.floor(sec / 60)}m`
  return `${Math.floor(sec / 3600)}h`
}

/** 龄的悬停写法：`<60s` 也给秒（悬停是全因，不是缩略）。 */
function ageLabel(sec: number): string {
  return fmtAge(sec) || `${Math.round(sec)}s`
}

/** 一条在飞 holder 的悬停事实（含「未登记持有者」点名，plan §3.3）。 */
function holderFacts(
  d: CourseOverviewRow['inflightDetail'][number],
  registered?: string[] | null,
): string {
  const bits = [`持有者 ${d.worker || '未知'}`]
  if (d.claimedAgo !== null) bits.push(`认领 ${ageLabel(d.claimedAgo)} 前`)
  if (d.computingAgo !== null) bits.push(`开算 ${ageLabel(d.computingAgo)} 前`)
  if (d.heartbeatAgo !== null) bits.push(`心跳 ${ageLabel(d.heartbeatAgo)} 前`)
  if (registered && d.worker && !registered.includes(d.worker)) {
    bits.push('不在 worker 登记表里')
  }
  return bits.join('，')
}

/** hub 派发态的悬停全因（§3.3 的顺序：派发事实 → 队列深度 → 预取窗口 → 训练侧原文）。 */
function hubDispatchTitle(
  hub: CourseOverviewRow,
  wait: string,
  registered?: string[] | null,
): string {
  const facts = hub.inflightDetail.length
    ? `hub 派发：${hub.inflightDetail.map((d) => holderFacts(d, registered)).join('；')}`
    : 'hub 派发：无在飞明细'
  const queue = `队列 ${hub.queuePending} 待领${hub.nextJob ? `（队首 ${hub.nextJob}）` : ''}`
  const win =
    hub.peeked === null
      ? '预取窗口不可知（旧版 hub）'
      : hub.peeked
        ? '在预取窗口内'
        : '不在预取窗口'
  return `${facts} · ${queue} · ${win} · ${wait}`
}

/** 最老的那条在飞（按认领龄；全缺 `claimed_ago` ⇒ null——不编龄）。 */
export function pickOldestInflight(row: {
  inflightDetail: CourseOverviewRow['inflightDetail']
}): CourseOverviewRow['inflightDetail'][number] | null {
  let best: CourseOverviewRow['inflightDetail'][number] | null = null
  for (const d of row.inflightDetail) {
    if (d.claimedAgo === null) continue
    if (best === null || d.claimedAgo > (best.claimedAgo ?? 0)) best = d
  }
  return best
}

/** 语义状态（**唯一派生输出**；渲染器只把它说成 pill 词或矩阵词）。 */
export type CourseStatusKind =
  /** 调度器读面里没有这门课（读面不可用/超时）——是「不知道」，不是「空闲」。 */
  | 'unknown'
  | 'paused'
  | 'done'
  | 'aborted'
  /** 课程配置不可开课（整课起不来；2026-10-05 plan/course-startup-recover §3.3）。
   *  不是「待进程」：进程起来也会被同一道校验拒启。 */
  | 'blocked'
  /** 已开课但共享 trainer 没在跑（启动服务进程就会入队）。 */
  | 'waiting-process'
  /** **接管中**（自主 worker 在跑这段）∧ 已有段内产物回传（★M4 词：云机执行中）。 */
  | 'autonomous-running'
  /** **接管中** ∧ 还没回传任何段内产物（★M4 词：等云机接管，含「导包软态」）。 */
  | 'autonomous-waiting'
  /** **接管掉线**（进度静默超阈）：自动恢复协作派发（★M4 词）。 */
  | 'autonomous-stale'
  /** hub 有在飞（未超阈）。 */
  | 'dispatch-inflight'
  /** hub 有在飞但龄超阈（有持有者，无进度）。 */
  | 'dispatch-stuck'
  /** hub 有活、没在飞、不在预取窗口（没人要跑）。 */
  | 'dispatch-no-taker'
  /** hub 有活且在预取窗口内（有人要跑）。 */
  | 'dispatch-prefetched'
  /** hub 有活但预取窗口不可知（旧 hub）——不可知 ≠ 无人取。 */
  | 'dispatch-queued'
  /** 本机在飞但 hub 无对应 job（本机 PPO，非远端）。 */
  | 'local-inflight'
  /** 训练侧等待四态。 */
  | 'collecting'
  | 'ready'
  | 'idle'
  /** 训练侧的「接管中」等待词（M2 的 `held`；详见 `loop-queue.ts::LoopWaitKind`）。 */
  | 'held-wait'
  /** 在训但 hub 不认（矩阵冲突档）。 */
  | 'hub-unregistered'
  /** hub 认且在派活、训练侧没进程（矩阵冲突档）。 */
  | 'hub-no-process'
  /** 未在训（没有进程、也没有 hub 冲突）。 */
  | 'not-training'
  /** 在训（hub 无应答时也能知道的粗档；矩阵用）。 */
  | 'training'

/** 这一词来自哪一类事实（R4-g 的来源标注；不是第二份判据）。
 *
 *  ★M4：`'intent'` 随意图表一起退役（没有「控制台意图 vs hub 事实」这层可漂移的东西了）。 */
export type CourseStatusSource = 'registry' | 'marker' | 'hub' | 'loop' | 'unknown'

/** 派生输出（pill 与矩阵**读同一份**；§3.10 的 `{state, source, stale, title}`）。 */
export interface CourseStatus {
  course: string
  kind: CourseStatusKind
  /** 状态词（pill 直接上屏；矩阵按 kind 映射到矩阵词表——语义同源，词表各自渲染）。 */
  text: string
  tone: CoursePillTone
  /** 悬停全文（判据 + 全因）。 */
  title: string
  source: CourseStatusSource
  /** 读面新鲜度标注：`null` = 本拍读成功；有值 = 上方事实是**上一拍/更旧**的值。 */
  stale: ReadStaleView | null
  /** 展示龄（`<60s`/无龄 ⇒ null）。 */
  age: string | null
  /** 矩阵侧的冲突档（`hub-unregistered` / `hub-no-process`；`null` = 无冲突）。 */
  conflict: 'hub-unregistered' | 'hub-no-process' | null
}

/** 派生输入。**不传行外事实**：authority 从 `ov` 行读（不猜，P0-2）。 */
export interface CourseStatusInput {
  course: string
  /** 训练侧队列行（`null` = 调度器读面里没有这门课）。 */
  lq: LoopQueueRow | null
  /** hub 侧总览行（`null` = 旧视图 / 没有总览）。 */
  ov: CourseOverviewRow | null
  /** hub 此刻在不在应答（机群级事实）。 */
  hubOnline: boolean
  /** **这门课此刻有存活进程**：pill 传共享 trainer 存活；矩阵由行自己推（`lq.training`）。 */
  trainerRunning: boolean
  /** 登记在册的 push worker id（holder 不在表里 ⇒ 悬停点名）。 */
  registeredWorkers?: string[] | null
  /** hub 读面新鲜度（`ParallelOverviewView.stale`）；缺省 = 本拍读成功。 */
  overviewStale?: ReadStaleView | null
  /** 训练侧读面新鲜度（`LoopQueueView.stale`）。 */
  queueStale?: ReadStaleView | null
  /** **账本尾行 `run_complete` 的停车态**（`stateView.loopCompletes[课]`；缺省/null = 没有）。
   *
   *  ★2026-10-06（用户报障「收官的课程，pill 仍显示推进中」）：只读读面按盘重建计划——停在
   *  某一轮的课（门禁 ABORT / 云机段末 / 预算跑满）指针仍指向下一轮、13 步表非空 ⇒ 报
   *  `ready` ⇒ pill「推进中」，而账本尾行明明写着 `run_complete`。停车态是服务端**既有**
   *  的派生事实（`collectLoopCompletes`，告警坞的「✅ 训练已完成」同源）——这里只消费它，
   *  不另立判据；resume 后尾行变新 ⇒ 条目自动消失（本派生随之回到训练侧词）。 */
  loopComplete?: LoopComplete | null
}

function statusOf(
  input: CourseStatusInput,
  kind: CourseStatusKind,
  text: string,
  tone: CoursePillTone,
  title: string,
  source: CourseStatusSource,
  extra: Partial<Pick<CourseStatus, 'age' | 'conflict'>> = {},
): CourseStatus {
  return {
    course: input.course,
    kind,
    text,
    tone,
    title,
    source,
    stale: input.overviewStale ?? input.queueStale ?? null,
    age: extra.age ?? null,
    conflict: extra.conflict ?? null,
  }
}

// ────────────────────────── 接管（hold）的词（★M4） ──────────────────────────
//
// 三个纯函数把一份 `HubHoldView` ＋ 段内轮数说成词/调子/悬停：**词表只有一处**（
// plan §3-M4 定的三词 + 一个「导包中」预警），pill 与矩阵都从这里读（R4-a 的纪律：
// 同一件事不得在两处各起一个名字）。

/** hold → 语义档。三态：live+有回传 ⇒ 云机执行中；live+零回传 ⇒ 等云机接管；stale ⇒ 接管掉线。 */
export function holdKind(
  hold: NonNullable<CourseOverviewRow['hold']>,
  rounds: number,
): CourseStatusKind {
  if (hold.state === 'stale') return 'autonomous-stale'
  return rounds > 0 ? 'autonomous-running' : 'autonomous-waiting'
}

/** hold → 展示词（pill 与矩阵同词；矩阵那一列另有自己的粗词，见 `course-matrix.ts`）。 */
export function holdWord(hold: NonNullable<CourseOverviewRow['hold']>, rounds: number): string {
  if (hold.state === 'stale') return '接管掉线'
  return rounds > 0 ? '云机执行中' : '等云机接管'
}

/** hold → 调子：执行中绿；等接管黄；**掉线黄不要红**——它已经自愈（协作派发恢复），不是故障。 */
export function holdTone(
  hold: NonNullable<CourseOverviewRow['hold']>,
  rounds: number,
): CoursePillTone {
  if (hold.state === 'stale') return 'y'
  return rounds > 0 ? 'g' : 'y'
}

/** hold → 悬停全文（把「谁在跑、多久没回传、掉线后发生了什么」一次说完）。 */
export function holdTitle(course: string, ov: CourseOverviewRow, wait: string): string {
  const hold = ov.hold
  if (!hold) return ''
  const who = hold.workerId || '未登记持有者'
  const age =
    hold.lastProgressAt > 0
      ? `最近一次进度 ${ageLabel(Date.now() / 1000 - hold.lastProgressAt)} 前`
      : '还没有进度信号'
  const rounds =
    ov.offlineRounds > 0
      ? `已回传段内 ${ov.offlineRounds} 轮（最新 it${ov.offlineLastIter ?? '—'}）`
      : '还没有任何段内产物回传'
  if (hold.state === 'stale') {
    return (
      `接管掉线：持有人「${who}」${age}（超进度阈）——**协作派发与本机采样已自动恢复**` +
      `（在队任务照常可领；被释放的 worker 若还活着，它的产物只归档）。${rounds}。` +
      `新自主盘可直接 claim 接管（无需人工）。${wait}`
    )
  }
  return (
    `${course} 正被自主 worker「${who}」接管（接管建立时 hold 才存在）：该课对协作 worker ` +
    `压下不派 + 本机不跑（held 等待）；${age}；${rounds}。` +
    '活性**只认进度信号**（心跳不算）——15 分钟无进度自动解除，到控制台可「强制解除接管」。' +
    `进度明细见课程矩阵的「接管」列 · ${wait}`
  )
}

/** 这门课是否「在训」（矩阵冲突判据；两侧同源：有训练侧行就信它，否则信 hub 行的开课标记）。 */
function isTraining(lq: LoopQueueRow | null, ov: CourseOverviewRow | null): boolean {
  return lq ? lq.training : (ov?.training ?? false)
}

/**
 * **唯一派生**：把「这门课现在归谁 / 在干什么」算成一个语义状态（+ 词 + 滞回标注）。
 *
 * 优先级（与 pill 的既有读序一致；前几档是确定性事实，压过「在等什么」）：
 *   ⓪ 账本尾行 `run_complete` 的停车态 ⇒ `done`（终态；它比「行缺失」更知道答案）；
 *   ① 调度器行缺失 ⇒ `unknown`（读面不可用，不是「空闲」）；
 *   ② 暂停意图 / 已收官 / 已中止 / **配置不可开课**；
 *   ③ **接管（hold）**（★M4：live = 云机执行中/等接管；stale = 接管掉线、已恢复协作）；
 *   ④ 已开课但没进程 ⇒ `waiting-process`；
 *   ⑤ hub 派发态（在飞 / 卡住 / 排队·无人取 / 预取中）；
 *   ⑥ 训练侧「在等什么」五态 + 接管等待（M2 的 `held`）。
 *
 *  ★M4（plan §3-M4）：原来的 ③「意图未生效」与 ④「hub 离线（回传）」两档都随课程去
 *  模式化消失——前者没了意图表，后者没了 `mode`；「谁在跑这门课」只剩 hold 一个真源。
 *
 * 冲突档（`hub-unregistered` / `hub-no-process`）与主词正交：矩阵状态列会把它们前置成
 * 警告词（那是矩阵独有的信号），pill 只把它写进悬停；**两者出自同一个 `isTraining` 判据**。
 */
export function courseStatus(input: CourseStatusInput): CourseStatus {
  const { lq, ov, hubOnline } = input
  // ⓪ 收官停车态（账本尾行 `run_complete`）：**终态**——先于「行缺失 / 在等什么」，
  //    也先于暂停意图（停车后「已暂停」既不准确，其文案「恢复走开课」也不成立：
  //    完赛态必须用**停课**清，直接重开不清）。
  if (input.loopComplete) {
    const done = input.loopComplete
    return statusOf(
      input,
      'done',
      '已收官',
      'gray',
      `训练已完成（${done.reason}）。${PARKED_RESUME_ADVICE}`,
      'loop',
      // 终态不参与 hub 冲突升级（矩阵会把 `hub-unregistered` 前置成「在训 · hub 未注册」——
      // 而课已停车，那个词只是在谎报算力在烧）。
      { conflict: null },
    )
  }
  const training = isTraining(lq, ov)
  const hubKnown = ov !== null && hubOnline
  const conflict: CourseStatus['conflict'] =
    training && hubKnown && !ov!.hubSeen
      ? 'hub-unregistered'
      : !training && hubKnown && ov!.hubSeen && !ov!.hold
        ? 'hub-no-process'
        : null
  /** 本课冲突档随词一起出（所有分支默认带上它；个别分支显式覆盖）——矩阵用它升级成警告词。 */
  const out = (
    i: CourseStatusInput,
    kind: CourseStatusKind,
    text: string,
    tone: CoursePillTone,
    title: string,
    source: CourseStatusSource,
    extra: Partial<Pick<CourseStatus, 'age' | 'conflict'>> = {},
  ): CourseStatus =>
    statusOf(i, kind, text, tone, title, source, {
      ...extra,
      conflict: extra.conflict === undefined ? conflict : extra.conflict,
    })

  // ① 训练侧行缺失：只剩 hub 侧事实。hub 不可达/没读到时是「未在训/在训」的粗档
  //    （不把「读不到课程表」读成「hub 不认这门课」——R4-g 的假诊断回归闸）。
  if (!lq) {
    if (!ov || !hubOnline) {
      return training
        ? out(
            input,
            'training',
            '在训',
            'g',
            '共享 trainer 在跑，且这门课未收官（进程存活 = registry，还算不算活 = python 队列状态）',
            'registry',
            { conflict: null },
          )
        : out(
            input,
            'not-training',
            '未在训',
            'gray',
            '没有存活的共享 trainer 进程，且训练侧只读视图不可用——这一行只有 hub 侧事实',
            'registry',
          )
    }
    if (training && !ov.hubSeen) {
      return out(
        input,
        'hub-unregistered',
        '在训',
        'y',
        'hub 的课程表里没有这门课——它的 PPO job 永远不会被派发（rollout 白跑）。' +
          '以 `--course <课>` 重启 hub，或把 hub 的课程表补上这门课',
        'hub',
        { conflict: 'hub-unregistered' },
      )
    }
    if (ov.hubSeen && !ov.hold && !training) {
      return out(
        input,
        'hub-no-process',
        'hub 已注册 · 无进程',
        'y',
        'hub 的课程表里有这门课且在线，但没有任何训练进程推进它——派给它的 job 没有人消费，' +
          '会一直堆在队列里（这份信号在分开的两张表上各自都是「正常」的）',
        'hub',
        { conflict: 'hub-no-process' },
      )
    }
    if (ov.hold) {
      return out(
        input,
        holdKind(ov.hold, ov.offlineRounds),
        holdWord(ov.hold, ov.offlineRounds),
        holdTone(ov.hold, ov.offlineRounds),
        holdTitle(input.course, ov, ''),
        'hub',
      )
    }
    if (ov.training) {
      return out(
        input,
        'training',
        '在训',
        'g',
        '共享 trainer 在跑，且这门课未收官（进程存活 = registry，还算不算活 = python 队列状态）',
        'registry',
      )
    }
    return out(
      input,
      'not-training',
      '未在训',
      'gray',
      '没有存活的共享 trainer 进程，且训练侧只读视图不可用——这一行只有 hub 侧事实',
      'registry',
    )
  }

  const wait = lq.waiting.text
  // ② 确定性事实优先于「在等什么」。
  if (lq.pausedIntent) {
    return out(
      input,
      'paused',
      '已暂停',
      'y',
      `暂停意图已写（${lq.pauseApplied ? '训练进程已停住这门课' : '训练进程还没读到它'}）。` +
        `恢复走「开课」· ${wait}`,
      'loop',
    )
  }
  if (lq.state === 'done') {
    return out(
      input,
      'done',
      '已收官',
      'gray',
      `本轮课程已收官（${wait}）——要接着跑就改大 iters 后重新开课。`,
      'loop',
    )
  }
  if (lq.state === 'aborted') {
    return out(input, 'aborted', '已中止', 'r', `训练进程已把该课标为「已中止」：${wait}`, 'loop')
  }
  // ★ 2026-10-05（plan/course-startup-recover §3.3 / §8.2）：配置**不可开课** ⇒ 红。
  // 「起不来」不是「待进程」——进程起来也会被同一道校验拒启。文案不写死 serve 侧事实
  // （评审 F4）：这是**配置面判据**，正在跑的课不受影响；人唯一的动作是修课程文件。
  // 位置在确定性事实档（暂停/收官/中止之后、意图漂移之前）：配置坏了则在线离线都跑不了。
  if (!lq.openable.ok || lq.waiting.kind === 'blocked') {
    const why = lq.openable.reason || wait || '读不到原因'
    return out(
      input,
      'blocked',
      '起不来',
      'r',
      `课程配置不可开课：${why}。这是课程文件配置问题（trainer 在跑时会整课跳过；` +
        '正在跑的课不受影响）——改好课程文件后会自动重试开跑，不需要重启 trainer。',
      'loop',
    )
  }
  const hub = ov
  // ③ **接管（hold）**：★M4 取代了原来的「意图 vs hub 事实」与「hub 离线（回传）」两档。
  //    它排在「进程没跑」之前：接管是 hub 侧事实，与本地进程死活正交——一门被云机接管的课
  //    不该因为本机 trainer 没起就显示「待进程」（它会误导操作员去启动本机进程，而该课此刻归云机）。
  if (hub?.hold) {
    return out(
      input,
      holdKind(hub.hold, hub.offlineRounds),
      holdWord(hub.hold, hub.offlineRounds),
      holdTone(hub.hold, hub.offlineRounds),
      holdTitle(input.course, hub, wait),
      'hub',
    )
  }
  // ③' **导包软态**（`pending_export`）：有人正给这门课造包，但**还没建 hold**——它不占任何闸
  //   （本机照跑、照派发），所以词要说得轻（「导包中」），不能画成「云机已接手」（那会让
  //   操作员以为本机该停）。位置在 hold 之后、调度词之前：它是「这件课即将被接管」的预告。
  if (hub?.pendingExport && !hub.hold) {
    return out(
      input,
      'autonomous-waiting',
      '导包中',
      'y',
      `${input.course} 有自主 worker 已认领（hub 的 pending_export，触发方 ` +
        `${hub.pendingExport.by || '未知'}）——控制台正在导出任务包；**包到手前不建接管**：` +
        '本机照跑、协作照派（软态不占闸）。包写好且 claim 成功后才进入「接管中」。' +
        ` · ${wait}`,
      'hub',
    )
  }
  // ⑤ 已开课但进程没跑（启动「服务进程」就会入队；不是故障，也不是空闲）。排在 hub 离线之后：
  //    在线课才是「等本机进程」的形状；离线课上面已经说完。
  if (!input.trainerRunning) {
    return out(
      input,
      'waiting-process',
      '待进程',
      'gray',
      '已开课（在调度课程表里），但共享 trainer 没在跑——启动「服务进程」后下一拍就会入队。' +
        `· ${wait}`,
      'registry',
      { conflict },
    )
  }
  // ⑥ hub 派发态：门条件 = 课程在线 ∧ hub 可达 ∧ `hubSeen` ∧（有在飞 ∨ 有排队）。
  //    否则回落训练侧词——旧 hub / hub 不可达时逐字段退化为今天的行为（不编状态）。
  const hubLive = !!hub && hub.hubSeen && (hub.inflight > 0 || hub.queuePending > 0)
  if (hubLive && hub) {
    const trainingJids = lq.inflight.map((x) => x.jid).filter((x): x is string => !!x)
    const hubJids = new Set(hub.inflightDetail.map((d) => d.jobId).filter(Boolean))
    const matched = trainingJids.some((j) => hubJids.has(j))
    // 本机 inflight：训练侧在飞但 hub 无对应 job ⇒ 本机 PPO。**压过后面的排队态**
    // ——否则本机在算的同时远端队列有活，会被误读成「没人取」。
    if (
      lq.waiting.kind === 'inflight' &&
      trainingJids.length > 0 &&
      !matched &&
      hub.inflight === 0
    ) {
      return out(
        input,
        'local-inflight',
        '等回传（本机）',
        'y',
        '本机在算（训练侧 inflight，但 hub 没有这门课的在飞 job）——非远端。' +
          hubDispatchTitle(hub, wait, input.registeredWorkers) +
          (conflict === 'hub-unregistered' ? ' ⚠ hub 的课程表里没有这门课。' : ''),
        'loop',
        { conflict },
      )
    }
    if (lq.waiting.kind === 'inflight' && trainingJids.length === 0 && hub.inflight === 0) {
      // 旧训练侧 WAL 没 jid：不做连接、不判本机，也不把「训练侧在等」说成「没人取」。
      return out(
        input,
        'local-inflight',
        '等回传',
        'y',
        `训练侧在飞但没有 jid（旧训练侧）——无法与 hub 在飞对号。${hubDispatchTitle(
          hub,
          wait,
          input.registeredWorkers,
        )}`,
        'loop',
        { conflict },
      )
    }
    if (hub.inflight > 0) {
      const age = pickOldestInflight(hub)?.claimedAgo ?? null
      if (age !== null && age > PILL_STUCK_SEC) {
        return out(
          input,
          'dispatch-stuck',
          STUCK_LABEL,
          'r',
          hubDispatchTitle(hub, wait, input.registeredWorkers),
          'hub',
          { age: fmtAge(age) || null },
        )
      }
      return out(
        input,
        'dispatch-inflight',
        '等回传',
        'y',
        hubDispatchTitle(hub, wait, input.registeredWorkers),
        'hub',
        { age: fmtAge(age) || null },
      )
    }
    // 到这里只可能是 `inflight == 0 ∧ queuePending > 0`（门条件）。
    if (hub.peeked === null) {
      return out(
        input,
        'dispatch-queued',
        '排队中',
        'y',
        `hub 预取窗口不可知（旧版 hub）——无法区分「没人要跑」与「预取中」。${hubDispatchTitle(
          hub,
          wait,
          input.registeredWorkers,
        )}`,
        'hub',
      )
    }
    if (hub.peeked) {
      return out(
        input,
        'dispatch-prefetched',
        '预取中',
        'g',
        `hub 有活且刚被 worker 预取扫到（在预取窗口内）。${hubDispatchTitle(
          hub,
          wait,
          input.registeredWorkers,
        )}`,
        'hub',
      )
    }
    return out(
      input,
      'dispatch-no-taker',
      NO_TAKER_LABEL,
      'y',
      `hub 有活、没有在飞、也不在预取窗口——没人要跑（与告警坞的 PPO 红条同一件事：` +
        `pill 说状态、告警坞在超时后升级）。${hubDispatchTitle(hub, wait, input.registeredWorkers)}`,
      'hub',
    )
  }
  // ⑦ 训练侧「在等什么」。
  // `blocked` 不在这里另列一档：上面 `!openable.ok || waiting.kind === 'blocked'` 已拦，
  // 列出即与窄化后的类型冲突（TS2678）——起不来的唯一出口就是上面的红档。
  switch (lq.waiting.kind) {
    // ★M4（词表定稿）：训练侧的接管等待词是 **`held-wait`**「接管中（云机）」——闲词 `offline`
    // 已随 rollout_src 写面一起退役（见 `LoopWaitKind` 头注）。
    // 判据是训练侧自己的 held（双通道：直问 hub ∨ 控制文件的缓存，见 `trainer/loop_hold.py`）。
    case 'held':
      return out(
        input,
        'held-wait',
        '接管中（云机）',
        'y',
        `本机不跑这门课：该课正被自主 worker 接管（作业照常发布、由协作 worker 取）；` +
          `15 分钟无回传会自动恢复协作派发。${wait}`,
        'loop',
        { conflict },
      )
    case 'inflight':
      return out(input, 'dispatch-inflight', '等回传', 'y', wait, 'loop', { conflict })
    case 'collect':
      return out(input, 'collecting', '采集中', 'g', wait, 'loop', { conflict })
    case 'ready':
      return out(input, 'ready', '推进中', 'g', wait, 'loop', { conflict })
    default:
      // `idle`：本轮无待办（账本已结算 / 刚开课还没起第一轮）——不是故障，也不活跃。
      return out(input, 'idle', '空闲', 'gray', wait, 'loop', { conflict })
  }
}
