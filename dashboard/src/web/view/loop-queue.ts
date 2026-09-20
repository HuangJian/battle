/** loop-queue.ts — 单进程训练调度器（supervisor）的每课队列视图（R2c-3 控制台接线）。
 *
 *  背景（plan/r2-loop-task-queue §7）：多课程并行后 `trainingLoop` 收敛为**一个进程**，
 *  它内部一台单线程调度器持有**每课一条任务队列**。于是控制台需要回答一个今天只能靠翻
 *  N 份日志回答的问题：**「这门课在等什么」**。
 *
 *  数据源 = 训练侧只读入口 `nn-training/run_rl_cluster.py --json`（同一个 `rl/loop_plan.py`，
 *  与 CLI 表逐字段同源）。**判据不在本层重算**：`waiting` 的文案由 python 侧
 *  `loop_plan.waiting_state` 单点给出——TS 里从 facts 再推一遍就是把同一条语义写第二遍，
 *  两边迟早各说各话（这正是 R2b 否决 `loop-state.json` 的同一条理由）。
 *
 *  本模块**只放类型与纯函数**（无 IO、无 node:、无 Bun）——容错解析是最值得单测的部分：
 *  python 侧可能比控制台新/旧一个版本，缺字段必须退化成空态而不是让整页 /api/state 500
 *  （与 hub 队列/隧道 A/B 的只读面容错口径一致）。
 */

/** 「在等什么」的取值域（与 python `loop_plan.WAIT_*` 逐字对应）。 */
export type LoopWaitKind = 'inflight' | 'collect' | 'idle' | 'ready'

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
  /** **控制台写下的暂停意图**（`tmp/loop-control.json`，按钮动作就改它）。 */
  pausedIntent: boolean
  /** **训练进程回执：它实际把这门课停着**（`loop-control.applied.json`，已按进程存活过滤）。
   *  意图与事实必须分开上屏：只看意图会把「进程没跑/还没读到」演成已停，只看事实则点了
   *  暂停毫无反馈。 */
  pauseApplied: boolean
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

const WAIT_KINDS: LoopWaitKind[] = ['inflight', 'collect', 'idle', 'ready']
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

/** 解析 `run_rl_cluster.py --json` 的 stdout → 视图。
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
}

/** 「在等什么」的悬停全文（按 kind 取）。 */
export function waitTitle(kind: LoopWaitKind): string {
  return WAIT_TITLES[kind]
}
