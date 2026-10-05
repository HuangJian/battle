/** loop-queue.ts — 单进程训练调度器（supervisor）的每课队列视图（R2c-3 控制台接线）。
 *
 *  背景（plan/r2-loop-task-queue §7）：多课程并行后 `trainingLoop` 收敛为**一个进程**，
 *  它内部一台单线程调度器持有**每课一条任务队列**。于是控制台需要回答一个今天只能靠翻
 *  N 份日志回答的问题：**「这门课在等什么」**。
 *
 *  数据源 = 训练侧只读入口 `nn-training/trainer/run_rl_cluster.py --json`（同一个 `trainer/loop_plan.py`，
 *  与 CLI 表逐字段同源）。**判据不在本层重算**：`waiting` 的文案由 python 侧
 *  `loop_plan.waiting_state` 单点给出——TS 里从 facts 再推一遍就是把同一条语义写第二遍，
 *  两边迟早各说各话（这正是 R2b 否决 `loop-state.json` 的同一条理由）。
 *
 *  本模块**只放类型与纯函数**（无 IO、无 node:、无 Bun）——容错解析是最值得单测的部分：
 *  python 侧可能比控制台新/旧一个版本，缺字段必须退化成空态而不是让整页 /api/state 500
 *  （与 hub 队列/隧道 A/B 的只读面容错口径一致）。
 */

import type { CourseOverviewRow, HubInflightView, ParallelOverviewView } from './course-overview'

/** 「在等什么」的取值域（与 python `loop_plan.WAIT_*` 逐字对应）。
 *
 *  ★ 2026-10-05（plan/course-startup-recover §3.2）：新增 `blocked` —— 课程配置**不可开课**
 *  （整课起不来）。它不是「第五种等待」：`idle` 说的是「本轮无待办」这种**正常空转稳态**，
 *  把「配置不可运行」塞进 `idle` 会让停机中的课和起不来的课长得一样（本次事故「分不清」的
 *  翻版）⇒ 新增一格。取值域是契约面：python `run_rl_cluster.py` 文件头点名了这条纪律。 */
export type LoopWaitKind = 'inflight' | 'collect' | 'idle' | 'ready' | 'blocked'

/** 该课队列状态（python `loop_scheduler`：ready / running / waiting / paused / aborted / done）。 */
export type LoopCourseState = 'ready' | 'running' | 'waiting' | 'paused' | 'aborted' | 'done'

/** 课程种类（python `loop_plan.course_kind`：判据 = `curricula/<课>.bc.jsonc` 是否存在）。
 *
 *  BC 与 RL 在**同一张卡片里并列**（用户口径），两者的**指针、粒度、在飞来源都不同**：
 *  BC 走 `bc_round_completed` + 账本 `job_pending` + 单个轮任务，RL 走 `iteration` +
 *  commit journal + 13 步表。行上不带这个字段，UI 就只能自己猜（猜错就把 BC 读成 RL，
 *  显示一排看着像真的、其实不存在的零）。 */
export type LoopCourseKind = 'rl' | 'bc'

/** 在飞的一条远端提交（`commit_journal.inflight()`：已发布未回传）。 */
export interface LoopInflightView {
  /** 提交相位（ppo_remote / …）。 */
  phase: string
  /** 轮次（字符串，与 WAL 同行）。 */
  round: string
  /** 远端 job 编号（发布后 attach 补上；旧 WAL 可能没有）。 */
  jid: string | null
  /** 运输方式（push / pull）。 */
  dispatch: string | null
  /** 它所在的 it 目录名（`it37`）。 */
  dir: string | null
}

/** 该课的关键事实（账本 + shard 目录派生；`—` 表示未知，不编造）。 */
export interface LoopCourseFactsView {
  /** 账本里已结算的轮数。 */
  iterations: number
  lastVerdict: string | null
  trainSecTotal: number
  softRemediateCount: number
  klStreak: number
  /** 当前轮已结算（manifest 落盘）的局数。 */
  gamesSettled: number
  /** 计划局数；0 = 未知（盘上今天没记这个数——见 python `waiting_state` 的诚实性说明）。 */
  gamesPlanned: number
}

export interface LoopQueueRow {
  course: string
  /** 课程种类（`rl` / `bc`）——默认 `rl`：**python 比控制台旧时（还没这个字段）**，把课当
   *  RL 渲染是保守方向：误判成 BC 会给一门真 RL 课贴上 BC 标签并说「一轮 = 一个任务」。 */
  kind: LoopCourseKind
  /** 这门课**此刻有存活的 trainingLoop 进程**（registry 为事实源，与控制台总览同口径）。
   *  它不是 python 给的：盘上事实（账本/inflight）看不出「进程还在不在」。
   *  为什么必须上卡：没在训的课也会有一套「可推进」的队列（它只是没人跑），
   *  不区分就会把「停了」读成「等外部」。 */
  training: boolean
  /** 下一次要跑的轮次（账本指针）。 */
  it: number
  state: LoopCourseState
  /** 队列里第一个待办步骤（空 = 本轮无待办）。 */
  current: string
  /** 待办步骤（顺序即依赖顺序）。 */
  pending: string[]
  inflight: LoopInflightView[]
  facts: LoopCourseFactsView
  /** **「在等什么」**：python 侧算好的结论 + 取值域（UI 按 kind 上色/排序）。 */
  waiting: { kind: LoopWaitKind; text: string }
  /** **只读可开课判据**（python `trainer/course_spec.py::course_openable`）：`ok=false` =
   *  课程配置不可开课（判据 = 开课同一条校验链；整课起不来）。`waiting.kind=blocked` 与
   *  告警坞红条都从它派生。**旧 python 没这个字段 ⇒ 退化成 `{ok:true,''}`**（宁可漏报，
   *  不可把旧版本读成「每门课都坏」）。 */
  openable: { ok: boolean; reason: string }
  /** **控制台写下的暂停意图**（`tmp/loop-control.json`，按钮动作就改它）。 */
  pausedIntent: boolean
  /** **训练进程回执：它实际把这门课停着**（`loop-control.applied.json`，已按进程存活过滤）。
   *  意图与事实必须分开上屏：只看意图会把「进程没跑/还没读到」演成已停，只看事实则点了
   *  暂停毫无反馈。 */
  pauseApplied: boolean
}

// ────────────────────────── 在训课程 pill（顶部行） ──────────────────────────

/** pill 圆点的色调（g=在跑 / y=有外部等待或暂停 / r=中止 / gray=没在推进）。 */
export type CoursePillTone = 'g' | 'y' | 'r' | 'gray'

/** 顶部「在训课程」pill 的一门课（用户 2026-09-20 口径：课程开训必须手动开）。
 *
 *  它是「顶部一次看全在训哪几门、各自到哪一轮」的最小事实：**课程名 + it 指针 + 一句状态**。
 *  与 LoopQueue 卡的差別是职责：那张卡回答「这一轮卡在哪一步」（逐课任务表），pill 只要求
 *  「一眼扫完 N 门课」——所以它一屏一行、可点选（切查看目标）、可停（非破坏停课）。
 *
 *  ★ `it` 用账本指针（下一轮）而不是「已结算轮数」：调度器 `loop_plan.plan_course` 的指针
 *  与它同源（轮号不重复计数，否则 pill 与卡片会差 1）。 */
export interface CoursePillView {
  course: string
  /** 课程种类（BC 课挂徽标：它不是 RL 的 13 步表）——缺省 `rl`（python 比控制台旧时）。 */
  kind: LoopCourseKind
  /** 账本指针（下一轮）；null = 队列视图里没这门课（读面不可用，不是「第 0 轮」）。 */
  it: number | null
  /** 短状态（2-4 字；悬停有整句）——顶部一行里放不下整句，但也不能只给个圆点。 */
  status: string
  /** 展示用的**龄**（`fmtAge`；`<60s` 为空 ⇒ 省略）。与 `status` 分开是为了布局单行。 */
  age?: string | null
  tone: CoursePillTone
  /** 悬停整句：调度器「在等什么」的原文 + 进程/意图事实（诊断入口，不重写语义）。 */
  title: string
}

/** 「卡住」的展示阈值（秒）。= hub `CLAIM_TTL_SEC`：连一个租约周期都走完了还没回传，
 *  与稳态 wall（58–73s）差 ≥4×。**纯展示层常量**——不得被 hub / 训练侧 import。 */
export const PILL_STUCK_SEC = 300

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

/** 最老的那条在飞（按认领龄；全缺 `claimed_ago` ⇒ null——不编龄）。 */
export function pickOldestInflight(row: {
  inflightDetail: HubInflightView[]
}): HubInflightView | null {
  let best: HubInflightView | null = null
  for (const d of row.inflightDetail) {
    if (d.claimedAgo === null) continue
    if (best === null || d.claimedAgo > (best.claimedAgo ?? 0)) best = d
  }
  return best
}

/** 龄的悬停写法：`<60s` 也给秒（悬停是全因，不是缩略）。 */
function ageLabel(sec: number): string {
  return fmtAge(sec) || `${Math.round(sec)}s`
}

/** 一条在飞 holder 的悬停事实（含「未登记持有者」点名，plan §3.3）。 */
function holderFacts(d: HubInflightView, registered?: string[] | null): string {
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

/** 从「已开课课程表 + 队列行 + 进程存活」推出 pill 行（纯函数，可单测）。
 *
 *  **入参就是 pill 的全集**：`courses` 是服务端 stamp 的已开课课程（开课标记为事实源），
 *  逐课去 `rows` 里找它的队列行——找不到时**不编一个假状态**（读面不可用是事实，
 *  显示「视图不可用」而不是「空闲」：后者会让操作员去查一个不存在的卡顿）。
 *
 *  状态优先级：暂停意图 > 收官 > 中止 > 进程未运行 > 「在等什么」。前四者都是**确定性事实**，
 *  只有最后一条来自 python 的 waiting 判据——一件事只有一个主人（不在 TS 里重算）。
 *  在线课若 hub 有派发事实（在飞 / 排队没人取），改由 **hub 观测面**说
 *  （等回传 / 卡住 / 排队·无人取 / 预取中）——见 plan/course-pill-precision §3.1。
 */
export function coursePills(input: {
  courses: string[]
  rows: LoopQueueRow[]
  /** 共享 trainer 是否在跑（进程事实，与「已开课」正交：开了课但进程没跑是合法稳态）。 */
  trainerRunning: boolean
  /** hub 侧事实（`stateView.overview`）：离线标记、**段内已回传轮数**、hub 是否认得这门课。
   *
   *  ★2026-09-23：由「离线课名集」升级为整个总览视图——旧形状只能回答「hub 说不说它离线」，
   *  于是**一件产物都没回传**的课也被写成「回传中」（用户报障：三个离线课里两个「回传中」
   *  一个「等回传」，而三个都还没被云机取走）。「回传中」是一个**进度断言**，必须有
   *  `offlineRounds > 0` 才配得上；0 回传只能说「等云机」。`null`/缺省 = hub 侧不可读
   *  （那时不编离线状态，退回本地「在等什么」）。 */
  overview?: ParallelOverviewView | null
  /** 控制台记录的每课 hub 派发**意图**（`stateView.courseModeIntents`）：与 hub 事实不一致
   *  = 「意图未生效」（2026-09-23 实测的那种静默失配）——pill 上照实点名，而不是替 hub
   *  那份 volatile 的表说话。`null`/缺省 = 无意图（不画漂移，不编状态）。 */
  modeIntents?: Record<string, 'online' | 'offline'> | null
  /** 登记在册的 push worker id（rl-config `nodes[].gpu_push` 的 id 集）：holder 不在表里
   *  ⇒ 悬停点名「不在 worker 登记表里」。`null`/缺省 = 名单不可知（不点名）。 */
  registeredWorkers?: string[] | null
}): CoursePillView[] {
  const byCourse = new Map(input.rows.map((r) => [r.course, r]))
  const hubByCourse = new Map((input.overview?.rows ?? []).map((r) => [r.course, r]))
  return input.courses.map((course) => {
    const r = byCourse.get(course)
    if (!r) {
      return {
        course,
        kind: 'rl' as LoopCourseKind,
        it: null,
        status: '视图不可用',
        tone: 'gray' as CoursePillTone,
        title:
          '调度器读面不可用（trainer/run_rl_cluster.py --json 读失败 / 超时）：这门课的进度未知。' +
          '停课不受它影响（停课只写暂停意图 + 删开课标记）。',
      }
    }
    const it = r.it
    const wait = r.waiting.text
    const kind = r.kind
    if (r.pausedIntent) {
      return {
        course,
        kind,
        it,
        status: '已暂停',
        tone: 'y' as CoursePillTone,
        title:
          `暂停意图已写（${r.pauseApplied ? '训练进程已停住这门课' : '训练进程还没读到它'}）。` +
          `恢复走「开课」· ${wait}`,
      }
    }
    if (r.state === 'done') {
      return {
        course,
        kind,
        it,
        status: '已收官',
        tone: 'gray' as CoursePillTone,
        title: `本轮课程已收官（${wait}）——要接着跑就改大 iters 后重新开课。`,
      }
    }
    if (r.state === 'aborted') {
      return {
        course,
        kind,
        it,
        status: '已中止',
        tone: 'r' as CoursePillTone,
        title: `训练进程已把该课标为「已中止」：${wait}`,
      }
    }
    // ★ 2026-10-05（plan/course-startup-recover §3.3 / §8.2）：配置**不可开课** ⇒ 红。
    // 「起不来」不是「待进程」——进程起来也会被同一道校验拒启。文案不写死 serve 侧事实
    // （评审 F4）：这是**配置面判据**，正在跑的课不受影响；人唯一的动作是修课程文件。
    if (!r.openable.ok || r.waiting.kind === 'blocked') {
      const why = r.openable.reason || wait || '读不到原因'
      return {
        course,
        kind,
        it,
        status: '起不来',
        tone: 'r' as CoursePillTone,
        title:
          `课程配置不可开课：${why}。这是课程文件配置问题（trainer 在跑时会整课跳过；` +
          '正在跑的课不受影响）——改好课程文件后会自动重试开跑，不需要重启 trainer。',
      }
    }
    if (!input.trainerRunning) {
      return {
        course,
        kind,
        it,
        status: '待进程',
        tone: 'gray' as CoursePillTone,
        title:
          '已开课（在调度课程表里），但共享 trainer 没在跑——启动「服务进程」后下一拍就会入队。' +
          `· ${wait}`,
      }
    }
    // ★2026-09-23：离线课不再一刀切「回传中」——那是**进度断言**，只有真收到过产物才配说。
    // 三个离线课都还没被云机取走时，旧口径会出现「回传中 ×2 + 等回传 ×1」（那个「等回传」
    // 是 hub 侧还留在 online 的漂移课，走的本地 waiting 词）。现在：
    //   意图 ≠ hub 事实 ⇒ 「意图未生效」（点名，操作员才知道该再推一次）
    //   hub 离线 ∧ 已回传 > 0 ⇒ 「回传中」（绿点）
    //   hub 离线 ∧ 0 回传 ⇒ 「等云机」（⚠ 还没取走包 —— 此前被说成「回传中」，读着像在跑）
    // 位置仍在确定性事实（暂停/收官/中止/待进程）之后，它们的优先级不被动摇。
    const hub = hubByCourse.get(course)
    const intent = input.modeIntents?.[course]
    if (intent && hub?.hubSeen && hub.offline !== (intent === 'offline')) {
      return {
        course,
        kind,
        it,
        status: '意图未生效',
        tone: 'y' as CoursePillTone,
        title:
          `控制台意图是「${intent === 'offline' ? '离线' : '在线'}」，而 hub 现在把 ${course} 当「${
            hub.offline ? '离线' : '在线'
          }」——意图没落地（常见成因：hub 刚重启，回灌跑在它发现这门课之前）。` +
          '在课程矩阵里点该课「切离线/切换成在线」再推一次（幂等），或点「hubServer」回灌全部意图。' +
          ` · ${wait}`,
      }
    }
    if (hub?.offline) {
      if (hub.offlineRounds > 0) {
        return {
          course,
          kind,
          it,
          status: '回传中',
          tone: 'g' as CoursePillTone,
          title:
            `hub 离线（只收回传）：本段由云机整段执行，已回传 ${hub.offlineRounds} 轮` +
            `${hub.offlineLastIter == null ? '' : `（最新 it${hub.offlineLastIter}）`} · ${wait}`,
        }
      }
      return {
        course,
        kind,
        it,
        status: '等云机',
        tone: 'y' as CoursePillTone,
        title:
          'hub 离线（只收回传）：本段交给云机整段执行，但**还没有任何段内产物回传**——' +
          '云机可能还没取走任务包、或还在跑第一轮（本地 hub 只收回传、不实时派发）。' +
          `进度与「最近多久没动」见课程矩阵的「段内」列 · ${wait}`,
      }
    }
    // ── hub 派发态（plan §3.1，2026-10-02 pill 精确化） ────────────────────────
    // 门条件：课程在线 ∧ hub 可达 ∧ `hubSeen` ∧（有在飞 ∨ 有排队）。否则回落训练侧词——
    // 旧 hub / hub 不可达时**逐字段退化为今天的行为**（不编状态）。
    const hubLive = !!hub && hub.hubSeen && (hub.inflight > 0 || hub.queuePending > 0)
    if (hubLive && hub) {
      const trainingJids = r.inflight.map((x) => x.jid).filter((x): x is string => !!x)
      const hubJids = new Set(hub.inflightDetail.map((d) => d.jobId).filter(Boolean))
      const matched = trainingJids.some((j) => hubJids.has(j))
      // 本机 inflight（评审 P2）：训练侧在飞但 hub 无对应 job ⇒ 本机 PPO。**压过 10′/11′**
      // ——否则本机在算的同时远端队列有活，会被误读成「没人取」。
      if (
        r.waiting.kind === 'inflight' &&
        trainingJids.length > 0 &&
        !matched &&
        hub.inflight === 0
      ) {
        return {
          course,
          kind,
          it,
          status: '等回传（本机）',
          tone: 'y' as CoursePillTone,
          title:
            '本机在算（训练侧 inflight，但 hub 没有这门课的在飞 job）——非远端。' +
            hubDispatchTitle(hub, wait, input.registeredWorkers),
        }
      }
      if (r.waiting.kind === 'inflight' && trainingJids.length === 0 && hub.inflight === 0) {
        // 旧训练侧 WAL 没 jid（§3.4）：不做连接、不判本机，也不把「训练侧在等」说成
        // 「没人取」——保持原「等回传」词，悬停注明无法对号。
        return {
          course,
          kind,
          it,
          status: '等回传',
          tone: 'y' as CoursePillTone,
          title: `训练侧在飞但没有 jid（旧训练侧）——无法与 hub 在飞对号。${hubDispatchTitle(
            hub,
            wait,
            input.registeredWorkers,
          )}`,
        }
      }
      if (hub.inflight > 0) {
        const age = pickOldestInflight(hub)?.claimedAgo ?? null
        if (age !== null && age > PILL_STUCK_SEC) {
          return {
            course,
            kind,
            it,
            status: STUCK_LABEL,
            age: fmtAge(age),
            tone: 'r' as CoursePillTone,
            title: hubDispatchTitle(hub, wait, input.registeredWorkers),
          }
        }
        return {
          course,
          kind,
          it,
          status: '等回传',
          age: fmtAge(age) || null,
          tone: 'y' as CoursePillTone,
          title: hubDispatchTitle(hub, wait, input.registeredWorkers),
        }
      }
      // 到这里只可能是 `inflight == 0 ∧ queuePending > 0`（门条件）。
      if (hub.peeked === null) {
        return {
          course,
          kind,
          it,
          status: '排队中',
          tone: 'y' as CoursePillTone,
          title: `hub 预取窗口不可知（旧版 hub）——无法区分「没人要跑」与「预取中」。${hubDispatchTitle(
            hub,
            wait,
            input.registeredWorkers,
          )}`,
        }
      }
      if (hub.peeked) {
        return {
          course,
          kind,
          it,
          status: '预取中',
          tone: 'g' as CoursePillTone,
          title: `hub 有活且刚被 worker 预取扫到（在预取窗口内）。${hubDispatchTitle(
            hub,
            wait,
            input.registeredWorkers,
          )}`,
        }
      }
      return {
        course,
        kind,
        it,
        status: NO_TAKER_LABEL,
        tone: 'y' as CoursePillTone,
        title:
          `hub 有活、没有在飞、也不在预取窗口——没人要跑（与告警坞的 PPO 红条同一件事：` +
          `pill 说状态、告警坞在超时后升级）。${hubDispatchTitle(hub, wait, input.registeredWorkers)}`,
      }
    }
    switch (r.waiting.kind) {
      case 'inflight':
        return {
          course,
          kind,
          it,
          status: '等回传',
          tone: 'y' as CoursePillTone,
          title: wait,
        }
      case 'collect':
        return { course, kind, it, status: '采集中', tone: 'g' as CoursePillTone, title: wait }
      case 'ready':
        return { course, kind, it, status: '推进中', tone: 'g' as CoursePillTone, title: wait }
      default:
        // `idle`：本轮无待办（账本已结算 / 刚开课还没起第一轮）——不是故障，也不活跃。
        return { course, kind, it, status: '空闲', tone: 'gray' as CoursePillTone, title: wait }
    }
  })
}

/** 本机重资源池占用（容量为定的票数：本机 PPO / eval 跨课排队 = 1）。 */
export interface LoopPoolView {
  held: number
  capacity: number
}

export interface LoopQueueView {
  /** 被资源池挡住（没空闲票）的课程名；单进程调度器此刻在等票。 */
  blockedCourses: string[]
  pools: Record<string, LoopPoolView>
  rows: LoopQueueRow[]
  /** 行里在训课程数（trainingLoop 进程存活）——卡片表头「在训 N/M」的来源。 */
  trainingCount: number
  /** 读取失败原因（python 侧异常 / 解释器缺失 / 输出不可解析）；UI 显空态 + 原因，不静默。 */
  error?: string
}

const WAIT_KINDS: LoopWaitKind[] = ['inflight', 'collect', 'idle', 'ready', 'blocked']
const STATES: LoopCourseState[] = ['ready', 'running', 'waiting', 'paused', 'aborted', 'done']

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/** 解析 `trainer/run_rl_cluster.py --json` 的 stdout → 视图。
 *
 *  返回 null = **形状不符**（不是「没有课程」）：null 时 UI 显空态，且 `error` 由调用方给。
 *  逐字段容错是刻意的：python 侧新增字段不该让控制台炸，缺字段退化成 0/空也不该。
 */
export function parseLoopQueue(raw: unknown): LoopQueueView | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (!Array.isArray(o.courses)) return null
  const rows: LoopQueueRow[] = []
  for (const item of o.courses as unknown[]) {
    if (!item || typeof item !== 'object') continue
    const r = item as Record<string, unknown>
    const course = str(r.course)
    if (!course) continue
    const factsRaw = (r.facts ?? {}) as Record<string, unknown>
    const wRaw = (r.waiting ?? {}) as Record<string, unknown>
    // ★ 别叫 `kind`：行上已有一个课程种类 `kind`（同名过一次，症状是 TS 把等待种类当种类）
    const waitKind = str(wRaw.kind) as LoopWaitKind
    const inflight: LoopInflightView[] = []
    if (Array.isArray(r.inflight)) {
      for (const it of r.inflight as unknown[]) {
        if (!it || typeof it !== 'object') continue
        const i = it as Record<string, unknown>
        inflight.push({
          phase: str(i.phase) || '?',
          round: str(i.round) || '?',
          jid: typeof i.jid === 'string' && i.jid ? i.jid : null,
          dispatch: typeof i.dispatch === 'string' && i.dispatch ? i.dispatch : null,
          dir: typeof i.dir === 'string' && i.dir ? i.dir : null,
        })
      }
    }
    const state = str(r.state) as LoopCourseState
    // 未知/缺失 kind ⇒ `rl`（保守方向，见 `LoopCourseKind` 注释）
    const kind = str(r.kind) === 'bc' ? 'bc' : 'rl'
    // 旧 python 缺 `openable`（比控制台旧一个版本）⇒ ok=true（不误报）；`ok` 只认字面 false。
    const opRaw = (r.openable ?? {}) as Record<string, unknown>
    const openable = { ok: opRaw.ok !== false, reason: str(opRaw.reason) }
    rows.push({
      course,
      kind,
      // 在训与否由服务端用 registry 事实补（`withTraining`）——解析层不知道进程状态。
      training: false,
      it: num(r.it),
      state: STATES.includes(state) ? state : 'ready',
      pausedIntent: false, // 由 `withPausedFacts` 补（解析层不知道控制文件）
      pauseApplied: false,
      current: str(r.current),
      pending: strList(r.pending),
      inflight,
      facts: {
        iterations: num(factsRaw.iterations),
        lastVerdict: typeof factsRaw.last_verdict === 'string' ? factsRaw.last_verdict : null,
        trainSecTotal: num(factsRaw.train_sec_total),
        softRemediateCount: num(factsRaw.soft_remediate_count),
        klStreak: num(factsRaw.kl_streak),
        gamesSettled: num(factsRaw.games_settled),
        gamesPlanned: num(factsRaw.games_planned),
      },
      // 未知 kind（python 侧新增一类等待）⇒ 退化成 ready 的显示语义，但**保留文案**：
      // 宁可少一个颜色，不可把「在等什么」整句丢掉（那句话才是卡片的产出）。
      waiting: {
        kind: WAIT_KINDS.includes(waitKind) ? waitKind : 'ready',
        text: str(wRaw.text),
      },
      openable,
    })
  }
  const pools: Record<string, LoopPoolView> = {}
  const poolsRaw = (o.pools ?? {}) as Record<string, unknown>
  for (const [name, v] of Object.entries(poolsRaw)) {
    if (!v || typeof v !== 'object') continue
    const p = v as Record<string, unknown>
    pools[name] = { held: num(p.held), capacity: num(p.capacity) }
  }
  const blockedRaw = Array.isArray(o.blocked) ? (o.blocked as unknown[]) : []
  return {
    blockedCourses: blockedRaw.filter((x): x is string => typeof x === 'string'),
    pools,
    rows,
    trainingCount: 0,
  }
}

/** 把**控制面事实**（意图 + 实际生效）并进行。
 *
 *  意图与事实分开存：训练进程每拍才读一次控制文件，且它可能根本没在跑（那时意图就是
 *  「等进程起来才生效」）。分开之后 UI 才能如实说「待生效」而不是骗人地说「已暂停」。
 */
export function withPausedFacts(
  view: LoopQueueView,
  intent: string[],
  applied: string[],
): LoopQueueView {
  const want = new Set(intent)
  const did = new Set(applied)
  return {
    ...view,
    rows: view.rows.map((r) => ({
      ...r,
      pausedIntent: want.has(r.course),
      pauseApplied: did.has(r.course),
    })),
  }
}

/** 暂停态四值：意图与事实的四种组合各是一个真实且不同的局面。 */
export type LoopPauseState = 'paused' | 'pending' | 'resuming' | 'running'

export function pauseState(row: LoopQueueRow): LoopPauseState {
  if (row.pauseApplied) return row.pausedIntent ? 'paused' : 'resuming'
  return row.pausedIntent ? 'pending' : 'running'
}

/** 按钮文案：**按意图定方向**（按钮只改意图文件）。已生效时说「恢复」，未生效时说「取消暂停」
 *  ——两者都是「去掉意图」，但前者的真实语义是恢复训练。 */
export function pauseLabel(row: LoopQueueRow): string {
  if (!row.pausedIntent) return '暂停'
  return row.pauseApplied ? '恢复' : '取消暂停'
}

/** 按钮悬停解释。`pending` 要把「为什么还没生效」说清楚（进程没跑 / 还没轮到读）。 */
export function pauseTitle(row: LoopQueueRow): string {
  switch (pauseState(row)) {
    case 'paused':
      return '已生效：调度器不再推进这门课（队列与账本保留），点一下恢复'
    case 'resuming':
      return '恢复已请求，训练进程下一拍接着跑（回执还是上一拍的）'
    case 'pending':
      return row.training
        ? '暂停意图已写入控制文件：训练进程每拍读一次，下一拍生效'
        : '暂停意图已写入控制文件，但当前没有存活的 trainingLoop 进程——起进程时才会生效'
    default:
      return '暂停这门课：训练进程不再推进它（rollout / 本机 PPO / 预采全停），队列与账本保留，随时可恢复'
  }
}

/** 事实徽标（意图未生效/正在生效时才上屏；没有可说的就返回 null）。
 *
 *  `tone` 而非类名：徽章词表已随 P1 收敛到 `StatusRow` 的 `tc-badge--<tone>`，
 *  这里给出的是**语义**（已暂停 = info、待生效 = warn），映射成什么类由原语决定。 */
export function pauseBadge(row: LoopQueueRow): { text: string; tone: PauseBadgeTone } | null {
  switch (pauseState(row)) {
    case 'paused':
      return { text: '已暂停', tone: 'info' }
    case 'pending':
      return { text: '待生效', tone: 'warn' }
    case 'resuming':
      return { text: '恢复中', tone: 'warn' }
    default:
      return null
  }
}

/** 事实徽章的语义档（`info` = 正常态、`warn` = 与意图不一致需注意）。 */
export type PauseBadgeTone = 'info' | 'warn'

/** **「在训」的判据**（共享 trainer 时代）：**调度器进程活着** ∧ 这门课**没被收官**。
 *
 *  为什么不再看 registry 的每课条目：`trainingLoop` 已收敛为**一个**进程（2026-09-19 /
 *  R3-5），账本里只有 `['']` 一个槽 —— 按课查存活只会得到「一门课都没在训」这个假事实。
 *  现在两半各来自它能回答的那一半：进程存活 = registry（唯一的事实源），「这一课还有没有活」
 *  = python 给的队列状态（`state` 的 `done`/`aborted` 即收官）。
 *
 *  `schedulerAlive = false` ⇒ 全空：没人在跑时，盘上那套「可推进」的队列只是**计划**，
 *  把它读成「在训」正是这张卡片最想避免的那种误读。 */
export function trainingFromQueue(view: LoopQueueView, schedulerAlive: boolean): string[] {
  if (!schedulerAlive) return []
  return view.rows.filter((r) => r.state !== 'done' && r.state !== 'aborted').map((r) => r.course)
}

/** 把「在训」事实并进行，并统计在训数。
 *
 *  两个事实源各给一半：python 给「这门课这一轮卡在哪」（含它还有没有活），registry 给
 *  「调度器此刻在不在跑」。合并放在视图层（纯函数、可单测），服务端只负责把两边凑到一起。
 */
export function withTraining(view: LoopQueueView, training: string[]): LoopQueueView {
  const live = new Set(training)
  const rows = view.rows.map((r) => ({ ...r, training: live.has(r.course) }))
  return { ...view, rows, trainingCount: rows.filter((r) => r.training).length }
}

/** 种类徽标：BC 课才上屏（大多数课是 RL，给 RL 也挂一个标签就是噪声）。
 *
 *  它存在的理由很具体：同一张卡片里 BC 行与 RL 行的**形状不同**（BC 只有「一轮 = 一个
 *  任务」、没有门禁 verdict、指标在 `bc_epoch`/`bc_eval` 事件里）——不标出来，操作员会把
 *  「待办 1」读成「这门课没活了」。
 */
export function kindBadge(row: LoopQueueRow): { text: string; tone: 'a'; title: string } | null {
  if (row.kind !== 'bc') return null
  return {
    text: 'BC',
    // accent 而非灰：同表里 BC 行与 RL 行的形状不同，撞上灰徽章会被当成噪声略过。
    tone: 'a',
    title:
      'BC（行为克隆）课程：一轮 = 采集语料 → 发布 job → 等 GPU 回传 → 落位归档；' +
      '没有 RL 的门禁 / verdict / 13 步表，指标看 bc_epoch / bc_eval 账本事件',
  }
}

/** 「下一步」列的悬停全文：BC 的「下一步」是一个**整轮**（不是 RL 的某个步骤）。 */
export function stepTitle(row: LoopQueueRow): string {
  if (row.kind === 'bc') {
    return row.current
      ? `下一步：跑完这一轮 BC（采集语料 → 发布 job → 等 GPU 回传 → 落位归档）· 轮指针 it${row.it}`
      : `这一轮没有待办（BC 账本已结算 / 未开训）· 轮指针 it${row.it}`
  }
  return `下一步：${row.current || '（本轮无待办）'}`
}

/** 待办计数列的悬停全文（BC 一「轮」就是一个任务，说「步」会让人以为还有别的步骤）。 */
export function pendingTitle(row: LoopQueueRow): string {
  if (row.kind === 'bc') {
    return `BC 课一轮 = 一个任务（${row.pending.length} 个待办）：采集语料 → 发布 job → 等 GPU 回传 → 落位归档；不像 RL 那样拆成 13 步`
  }
  return `待办 ${row.pending.length} 步（顺序即依赖顺序）：${row.pending.join(' → ')}`
}

/** 未在训那一行的悬停全文（导出给用例断言，避免文案与断言两处漂移）。
 *
 *  它说清两件事：为什么是「未在训」（**盘上事实**推出来的，不是没人跑），以及下面那些指针/
 *  待办是不是真实读数（它们仍然是——只是没人执行）。 */
export const STOPPED_TITLE =
  '没有存活的共享 trainer 进程——下面是**盘上事实**推出的队列状态（若交给调度器会怎么做）'

/** 「在等什么」的修饰类（课程矩阵按 kind 上色；`''` = 不上色）。
 *
 *  上色是**读面**的事（运维先看谁在等外部），不是调度语义——故留在视图层，纯函数可测。
 *  曾经这里还有一个 `sortByWaiting`（按等待种类重排行），从未被任何面板接上，
 *  随「并行课程总览 + 训练调度器」合并成课程矩阵时一并删除：矩阵的行序要保持稳定
 *  （行随等待状态跳来跳去对扫读的伤害大于「等外部的排前面」那点收益）。 */
export function waitCls(kind: LoopWaitKind): string {
  switch (kind) {
    // 在飞 = 结果在别的进程/机器上，运维唯一能干预的一类 → 最醒目
    case 'inflight':
      return 'tc-mx__wait--inflight'
    case 'collect':
      return 'tc-mx__wait--collect'
    case 'idle':
      return 'tc-mx__wait--idle'
    // 起不来 = 人去修课程文件（唯一动作），比「在等外部」更该被一眼看见 ⇒ 红。
    case 'blocked':
      return 'tc-mx__wait--blocked'
    default:
      return ''
  }
}

/** 「在等什么」的悬停全文（kind 的语义在 python `loop_plan.waiting_state` 里定死）。
 *
 *  与文案分成两个出口：矩阵既要「那句话」（这是卡片的产出），也要「为什么这么说」
 *  （判据在哪算的），两处引用同一个常量以防漂移。 */
export const WAIT_TITLES: Record<LoopWaitKind, string> = {
  inflight: '已发布的 job 还没回传——结果在 GPU worker / 云机上；换节点或检查 worker 日志',
  collect: '本轮采集还在落盘（局数来自 it<N>/ 下的 manifest，配额只有课程计划知道）',
  idle: '本轮没有待办：账本已结算这一轮，或这门课还没开训',
  ready: '盘上事实看不出外部等待——没有在飞 job，采集也没在跑',
  blocked:
    '课程配置不可开课——整课起不来（不是「在等外部」）。判据 = 开课同一条校验链；' +
    '改好课程文件后会自动重试开跑，不需要重启 trainer',
}

/** 「在等什么」的悬停全文（按 kind 取）。 */
export function waitTitle(kind: LoopWaitKind): string {
  return WAIT_TITLES[kind]
}
