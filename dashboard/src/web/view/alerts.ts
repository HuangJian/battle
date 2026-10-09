/** alerts.ts — 告警坞的**读面**：把首页原先各自独立渲染的横幅收敛成一张有序的条目表（问题 C4）。
 *  纯函数，无 IO、无 localStorage（acks 由调用方读出来传进去）。
 *
 *  ## 为什么要收成一个坞（而不是继续加横幅）
 *
 *  合并前首页最多能同时堆 6 条**同权重**的横幅：停机（红）· 停机已恢复（灰）· 训练已完成（灰）·
 *  PPO 排队超时（红）· 课程编辑被拒（红）· 只读模式（蓝）。它们在 DOM 里是 6 个平级的 div，
 *  于是两个后果：
 *
 *  1. **顺序是巧合，不是优先级** —— 红横幅可能排在蓝横幅下面，而「停机」与「只读提示」显然
 *     不该同权；操作员的视线路径由代码书写顺序决定。
 *  2. **它们恰好在你最需要看主内容时把主内容推出首屏** —— 6 条各占一行，1920 屏上第一屏
 *     就只剩横幅了。
 *
 *  收成坞之后：按严重度**排序**（err > warn > info > 历史），默认只渲染 2 条，其余折成
 *  「还有 N 条 ▸」。**空的时候整坞不出现**（不留空壳，也不再有一条「没有告警」的占位横幅）。
 *
 *  ## 每条的形状（四段，与 docs/dashboard-redesign.md §3.3 一致）
 *
 *      `图标 · 一句话结论 ·（依据：指标/时间/操作指引）· 动作`
 *
 *  `title` 是**只读第一行就能决策**的结论；`detail` 保留原来那句话的全部信息（指标、时刻、
 *  该怎么处理）—— 收窄的不是信息量，是**排版层级**。原有的长句一字未删，只是从「整条横幅」
 *  降为「第二条弱化行」。
 *
 *  ## 可见范围（2026-10-03，plan/dashboard-banner-global §4.2）
 *
 *  **有恢复动作的瞬时态按课过滤；无动作的终态全局成列。** 停机横幅可「立即恢复」（只作用于本课）
 *  且会自愈 ⇒ 只弹当前课自洽；收官横幅没有动作、不会自愈（尾行不被顶掉就一直显示）⇒ 必须全局
 *  按课成列，否则「它已经跑完了」在多课场景**不可达**（2026-10-02：k25/k10 相继收官，不切课看不到）。
 */

import type { CloudHaltView, DiskFactsView, LoopComplete } from './console-types'
import type { OfflineLeaseView, OfflineStalledView } from './course-overview'
import { fmtTs } from './format'
import { alertAckKey, cloudHaltAckKey, visibleCloudHalts } from './interaction'
// 与 pill **同一个词**（2026-10-02 口径对齐，plan/course-pill-precision §6）：告警坞的 PPO 红条
// 与 pill 的「排队·无人取」说的是同一件事（有活、没人认领）；「卡住」是另一类（有持有者但
// 无进度）。两处各起一个名字 = 迟早漂开，所以词只在 `course-status.ts` 定义一次。
import type { LoopQueueRow } from './loop-queue'
import { NO_TAKER_LABEL, PARKED_RESUME_ADVICE, STUCK_LABEL } from './course-status'

/** 告警严重度（排序即这个顺序；`history` = 已恢复/已完成这类留痕）。 */
export type AlertSeverity = 'err' | 'warn' | 'info' | 'history'

export const ALERT_SEVERITY_RANK: Record<AlertSeverity, number> = {
  err: 0,
  warn: 1,
  info: 2,
  history: 3,
}

/** 坞默认渲染几条，其余折叠（§3.3：默认 2 条）。 */
export const ALERT_DOCK_DEFAULT_VISIBLE = 2

/** 条目上的动作。两种语义**必须分开**：
 *  - `resume`：真去调 API 改训练状态（横幅上的「立即恢复」）；
 *  - `ack`：只写本地已读（「知道了」），不碰服务端。 */
export interface AlertAction {
  kind: 'resume' | 'ack'
  label: string
  /** `kind='resume'`：API 动作名 + body。 */
  act?: string
  body?: Record<string, unknown>
  /** `kind='ack'`：已读键（按**事件身份**，新事件是新键——见 `alertAckKey`；只读提示是会话级字面键）。 */
  ackKey?: string
  title?: string
  /** 主按钮（每条最多一个；「知道了」永远是次按钮）。 */
  primary?: boolean
}

export interface AlertItem {
  /** 稳定标识（渲染 key；同一局面重复渲染不重排）。 */
  id: string
  severity: AlertSeverity
  icon: string
  /** 一句话结论。 */
  title: string
  /** 依据：指标 / 时间 / 操作指引。 */
  detail: string
  /** 无障碍角色：需要打断的用 `alert`，其余 `status`（读屏器不打断）。 */
  role: 'alert' | 'status'
  actions: AlertAction[]
  /** 一键复制全文（三行纯文本，2026-10-03 G4）：由 `withCopy` 统一派生，组件不许自己再拼一遍。 */
  copyText: string
}

/** 给条目补上复制文本（G4）：`title` + `detail` + 一行元信息（课名/条目 id/严重度）。
 *
 *  纯文本三行、无 Markdown——「能直接贴进日志或报告的一段话」就是它的验收口径。
 *  `id` 进 meta 是有意的（排障时能把这段文字定位回 `alerts.ts` 的哪一条）。 */
function withCopy(a: Omit<AlertItem, 'copyText'>, course: string): AlertItem {
  const meta = [course ? `课程 ${course}` : '', `条目 ${a.id}`, `严重度 ${a.severity}`]
    .filter(Boolean)
    .join(' · ')
  return { ...a, copyText: `${a.title}\n${a.detail}\n（${meta}）` }
}

/** 严重度序（稳定：同级保持输入顺序）。 */
export function sortAlerts(items: AlertItem[]): AlertItem[] {
  return items
    .map((a, i) => ({ a, i }))
    .sort(
      (x, y) => ALERT_SEVERITY_RANK[x.a.severity] - ALERT_SEVERITY_RANK[y.a.severity] || x.i - y.i,
    )
    .map((x) => x.a)
}

/** 坞的可见/折叠切分（纯函数：组件与用例共用同一份判据，免得「折叠了几条」两处各算一遍）。 */
export function alertDockSplit(
  items: AlertItem[],
  expanded: boolean,
  cap = ALERT_DOCK_DEFAULT_VISIBLE,
): { visible: AlertItem[]; hidden: AlertItem[] } {
  const sorted = sortAlerts(items)
  if (expanded || sorted.length <= cap) return { visible: sorted, hidden: [] }
  return { visible: sorted.slice(0, cap), hidden: sorted.slice(cap) }
}

/** 构建输入：**全是首页已有的视图字段**（不新增服务端契约）。 */
export interface AlertInput {
  cloudHalts: Record<string, CloudHaltView> | undefined
  /** 当前查看课程（停机记录按课过滤）。 */
  viewing: string
  /** 已读键（由调用方从 localStorage 读出）。 */
  acks: string[]
  /** 训练正常完成停车（`ConsoleStateView.loopCompletes` 原样传入；按课成列，S1）。 */
  loopCompletes?: Record<string, LoopComplete> | null
  ppoQueueStall?: { jobId: string; waitedSec: number; it: number | null } | null
  /** 离线课程停滞（`/admin/offline.stalled`；T8）：自动交接把本机停采后的**静默停摆**。
   *
   *  这是自动化的固有代价，必须显式付——`null`/缺省 = hub 不可达或旧版（**不可知 ≠ 没停**，
   *  什么都不画；hub 的可用性由课程矩阵表头的「hub 无应答」单独占位）。 */
  offlineStalls?: OfflineStalledView[] | null
  /** 逐课程离线租约（`/admin/offline.leases`；P2-1）：告警文案补 `stale-holder`（可接管）/
   *  `revoked`（已撤租）两态——两态都不是「云机死透」：前者 hub 会自动回收、后者新盘直接覆盖。
   *  `null`/缺省 = 旧 hub 没上报 ⇒ 文案保持原样（不编租约事实）。 */
  offlineLeases?: Record<string, OfflineLeaseView> | null
  /** `at` = 事件时刻（账本 `time`）：ack 事件身份用它；缺省回退字段签名（旧夹具）。 */
  courseEdit?: { verdict: string; fields: string[]; at?: string } | null
  /** 调度器每课队列行（`stateView.loopQueue.rows`）——第 8 类「课程配置不可开课」的取数面
   *  （2026-10-05，plan/course-startup-recover §3.3/§4.3）。`null`/缺省 = 读面不可用
   *  （旧控制台 / python 读失败）：什么都不画，**不编**「起不来」。 */
  loopQueueRows?: LoopQueueRow[] | null
  /** 本机磁盘水位（`ConsoleStateView.selfDisk`；plan/self-node-disk-alert）。
   *
   *  `null`/缺省 = 旧 agent 没报 / self 行没探到 ⇒ **不出条目、不推算**（不可知 ≠ ok）。
   *  五字段（含两条阈值）由 agent 给，告警坞只呈现与 ack，不硬编码 MB。 */
  selfDisk?: DiskFactsView | null
  readOnly: boolean
  /** 只读提示是否已被关掉（写盘的状态由调用方给）。 */
  roDismissed: boolean
  /** 现刻（`Date.now()`）——相对时间/时刻文案要它，传进来才能单测。 */
  now: number
}

/** 全部条目（未排序；排序由 `sortAlerts` / `alertDockSplit` 负责）。 */
export function buildAlerts(input: AlertInput): AlertItem[] {
  return [
    // 磁盘排最前（同严重度内按输入顺序稳定排序）：它和停机同为 `err` 时，坞默认只展开 2 条，
    // 而低盘是**会自己变得更糟**的那一条，不能被折进「还有 N 条」。
    ...selfDiskAlerts(input),
    ...cloudHaltAlerts(input),
    ...loopCompleteAlerts(input),
    ...ppoStallAlerts(input),
    ...offlineStallAlerts(input),
    ...courseEditAlerts(input),
    ...courseStartupAlerts(input),
    ...readOnlyAlerts(input),
  ]
}

/** 本机磁盘水位（plan/self-node-disk-alert）：`warn` → 橙条，`critical` → 红条。
 *
 *  ★ 后果句由数字判（评审 F3）：回差带 `[floorMB, floorMB+hyst)` 里档位仍可能是 `critical`，
 *  而 agent 此刻**正常收活** ⇒ 「已在拒收作业」只在 `freeMB < floorMB` 时出现；档位只决定
 *  严重度与是否打断。
 *
 *  ★ ack 键 = 档位 × 进档时刻（评审 F2）：`acks` 是只增不减的本地表，纯档位键一旦被点过就
 *  永久静默；带上 `since` 后「回到 ok 再跌破」= 新键 ⇒ 必重弹。 */
function selfDiskAlerts(input: AlertInput): AlertItem[] {
  const d = input.selfDisk
  if (!d || d.level === 'ok') return []
  const ackKey = alertAckKey('self-disk', 'self', `${d.level}@${d.since}`)
  if (input.acks.includes(ackKey)) return []
  const belowFloor = d.freeMB < d.floorMB
  const levelWord = d.level === 'critical' ? '严重' : '预警'
  return [
    withCopy(
      {
        id: `self-disk-${d.level}`,
        severity: d.level === 'critical' ? 'err' : 'warn',
        icon: '💾',
        title: belowFloor
          ? `本机磁盘 ${d.freeMB}MB — 已在拒收作业（跌破地板 ${d.floorMB}MB）`
          : `本机磁盘 ${d.freeMB}MB（${levelWord}：预警档 ${d.warnMB}MB）`,
        detail:
          `${d.freeMB}MB 可用（预警档 <${d.warnMB}MB / 拒收地板 <${d.floorMB}MB）。` +
          (belowFloor
            ? '已跌破地板：agent 对全部作业回 503，trainer 连续瞬时失败达阈值即停派 self' +
              '（rollout 与 eval 两条链路）。'
            : '尚未跌破地板——agent 仍在正常收活；跌破后会对全部作业回 503，' +
              'trainer 连续瞬时失败达阈值即停派 self（rollout 与 eval 两条链路）。') +
          '处置：清 nn-training/tmp 下陈旧课程分片（节点统计抽屉里有本机盘余量读数）。',
        role: 'alert',
        actions: [{ kind: 'ack', label: '知道了', ackKey }],
      },
      '',
    ),
  ]
}

/** 云端停机（红条，可立即恢复 / 知道了）与已恢复的留痕（灰条，可知道了）。 */
function cloudHaltAlerts(input: AlertInput): AlertItem[] {
  const out: AlertItem[] = []
  const visible = visibleCloudHalts(input.cloudHalts, input.viewing)
  for (const [courseName, h] of visible) {
    const who = courseName ? `课程 ${courseName} ` : ''
    if (h.status === 'halted') {
      const ackKey = cloudHaltAckKey('halted', courseName, h.at)
      if (input.acks.includes(ackKey)) continue
      out.push(
        withCopy(
          {
            id: `halt-${courseName}`,
            severity: 'err',
            icon: '⚠',
            title: `${who}停机中（${h.reason}）`,
            // 原文一字未删：停不掉就照常执行这句是**操作员决定要不要干预**的依据。
            detail:
              '已向云机下发停机命令——云机先尝试停机；停不掉则照常执行任务（不闲置空烧）。' +
              '本地 hub/console 均正常。本课恢复训练会自动解除；其它课的停机状态见课程矩阵的徽标。',
            role: 'alert',
            actions: [
              {
                kind: 'resume',
                label: '立即恢复',
                act: 'cloud-resume',
                body: { course: courseName },
                primary: true,
              },
              { kind: 'ack', label: '知道了', ackKey },
            ],
          },
          courseName,
        ),
      )
      continue
    }
    // recovered：灰条留痕（历史）
    const ackKey = cloudHaltAckKey('recovered', courseName, h.clearedAt ?? '')
    if (input.acks.includes(ackKey)) continue
    out.push(
      withCopy(
        {
          id: `rec-${courseName}`,
          severity: 'history',
          icon: '✓',
          title: `${who}曾停机（${h.reason}）· 已恢复`,
          detail:
            `已恢复（${h.clearReason ?? '手动恢复'}，${fmtTs(new Date(h.clearedAt ?? '').getTime(), input.now)}）；` +
            '停机期间停不掉的云机继续工作，未闲置浪费。',
          role: 'status',
          actions: [{ kind: 'ack', label: '知道了', ackKey }],
        },
        courseName,
      ),
    )
  }
  return out
}

/** 训练正常完成并停车等重启（灰条留痕——它不是故障，是设计内停车）。
 *
 *  **全局按课成列**（2026-10-03 S1）：收官是终态、没有动作、不会自愈 ⇒ 只弹当前课等于让
 *  「它已经跑完了」在多课场景不可达。标题带课名（多课收官不加课名就是 N 条一模一样的
 *  「训练已完成」，比不可见更难排查）；顺序按**课程名**（确定性，与对象键序解耦）。 */
function loopCompleteAlerts(input: AlertInput): AlertItem[] {
  return Object.entries(input.loopCompletes ?? {})
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([course, done]) => ({
      course,
      done,
      ackKey: alertAckKey('loop-complete', course, done.at),
    }))
    .filter(({ ackKey }) => !input.acks.includes(ackKey))
    .map(({ course, done, ackKey }) =>
      withCopy(
        {
          id: `loop-complete:${course}`,
          severity: 'history',
          icon: '✅',
          title: `${course ? `课程 ${course} ` : ''}训练已完成（${done.reason}）`,
          // 与 pill 的停车态同句（`course-status.ts::PARKED_RESUME_ADVICE`）——同一盘上事实
          // 在两个 widget 上说话，文案只能一份。
          detail: PARKED_RESUME_ADVICE,
          role: 'status',
          actions: [{ kind: 'ack', label: '知道了', ackKey }],
        },
        course,
      ),
    )
}

/** PPO 排队·无人取超时（红条）：盘上 `claimed` 不存在且目录龄 >5min ⇒ 云端 worker 可能断连。
 *
 *  ★ 词与 pill 同源（`NO_TAKER_LABEL`）：两处说的是**同一件事**（有活、没人认领）——
 *  pill 立即说状态、本坞在超时后升级，**词只能一个**。detail 里主动点名 pill 的
 *  「卡住」（`STUCK_LABEL`）是**另一类**（有持有者但无进度，处置方向相反）——它们不是一个
 *  信号，不能被读成同一个词的两遍。 */
function ppoStallAlerts(input: AlertInput): AlertItem[] {
  const stall = input.ppoQueueStall
  if (!stall) return []
  // 事件身份 = 这个 job 的这一轮（同一 job 被重新领取后再次超时是新 jobId）。
  const ackKey = alertAckKey('ppo-stall', stall.jobId, stall.it == null ? '' : String(stall.it))
  if (input.acks.includes(ackKey)) return []
  const waited = `${Math.floor(stall.waitedSec / 60)} 分${stall.waitedSec % 60} 秒`
  return [
    withCopy(
      {
        id: 'ppo-queue-stall',
        severity: 'err',
        icon: '⚠',
        title: `PPO ${NO_TAKER_LABEL}：已等待 ${waited} 仍无 worker 领取`,
        detail:
          `job ${stall.it != null ? `it${stall.it}` : stall.jobId.slice(0, 12)}` +
          '——云端 worker 可能断连或未在轮询 hub。检查 Colab/Kaggle worker 日志与 hub 是否在线。' +
          `（「${STUCK_LABEL}」是另一类：**有持有者**但无进度，本条不覆盖——看顶部 pill 的悬停全因。）`,
        role: 'alert',
        actions: [{ kind: 'ack', label: '知道了', ackKey }],
      },
      '',
    ),
  ]
}

/** 自主接管的停摆告警（橙/红条；T8，plan/auto-offline-handoff §3.9）。
 *
 *  为什么必须显式付：云机领走整段后可能在领到租约前/中死掉、或控制台导包失败 ⇒
 *  「云机没跑 + 没人知道」。U3 明令不许自动回退，所以出口得有人看得见。
 *
 *  判据全部来自已有事实（hub 侧 `stall_verdict`）：`pending-export` = 有人认领但**没有接管**、
 *  无新产物、超导包窗（云机没接手，红）；`running-stale` = 有接管但进度超阈值（可能只是长轮，橙）。
 *
 *  ★M4（plan/worker-type-dispatch-model §3-M4）：文案与动作随「课程不再分在线/离线」重写—— *  ① 旧模型里「已翻离线 ⇒ 本机立刻停采」那条腿已退役：`pending_export` **不占闸**，
 *     本机采样与协作派发照跑（详见 plan §5 风险表）——它仍是红条（云机在饿着），但不再是
 *     「本机停摆 ⇒ 算力全丢」那种红；
 *  ② 动作从已退役的 `unsetCourseMode`（清 pin/claim 记账）换成 **`releaseCourseHold`**
 *     （强制解除接管，`release_hold=1` → 立墓碑 + 清租约），且只在本课真有接管时才给：
 *     没有接管的课（pending-export）按它会得到 409「本来就没有接管」——那是假承诺。 */
function offlineStallAlerts(input: AlertInput): AlertItem[] {
  const out: AlertItem[] = []
  for (const s of input.offlineStalls ?? []) {
    // 事件身份：翻 mode 时刻（静默停摆）/ 最近补传产物 mtime（有租约但无进度）。
    const ackKey = alertAckKey(
      'offline-stall',
      s.course,
      String(s.why === 'pending-export' ? s.flippedAt : s.lastMtime),
    )
    if (input.acks.includes(ackKey)) continue
    const silent = s.why === 'pending-export'
    const mins = Math.max(0, Math.round(s.ageSec / 60))
    const ageText = mins >= 60 ? `${Math.floor(mins / 60)} 小时 ${mins % 60} 分` : `${mins} 分钟`
    const who = s.holder ? `${s.holder} ` : ''
    // ★P2-1：租约两态的文案（stale-holder = 新盘可直接接管；revoked = 已撤租墓碑）。
    //   两态都**不是**「云机死透」——不点名会把可自愈的局面写成需要人处理的故障。
    const lease = input.offlineLeases?.[s.course]
    const leaseNote = !lease
      ? ''
      : lease.revoked
        ? `租约状态：已撤租（墓碑）——旧持有者「${lease.workerId || '?'}」下次心跳会收 409 revoked；` +
          '新盘 claim 可直接覆盖墓碑，不需要人工清理。'
        : lease.stale
          ? `租约状态：持有者已静默 ${Math.round(lease.silentSec)}s 超阈（可接管）——` +
            'hub 在新盘 claim 时会自动回收，通常在下一拍自愈（无需人工干预）。'
          : ''
    out.push(
      withCopy(
        {
          id: `offline-stall-${s.course}`,
          severity: silent ? 'err' : 'warn',
          icon: '⚠',
          title: silent
            ? `${s.course} 没人接管 ${ageText}：既没有 hold 也没有新产物——云机没接手`
            : `${s.course} 接管卡住：${who}持有接管但 ${ageText} 没有新进度`,
          detail:
            (silent
              ? '有自主 worker 认领过这门课（当时缺包），但到现在还没建立接管（既没有 hold、也没有新产物）。' +
                '三条出路：'
              : '接管还在（hold live）但进度静默超阈——hub 判掉线后会**自动**恢复协作派发与本机采样。' +
                '要立刻收场就点下面的「强制解除接管」（立墓碑，新自主盘可当场 claim）。三条出路：') +
            '① 云机重连继续（它一上线就会在 /offline/tasks 再看到这门课' +
            (silent ? '，hub 会随即请控制台导包' : '') +
            '）；② 手工导入结果包（课程矩阵行内的「导入训练结果」——导入后会自动评估）' +
            (silent
              ? '；③ **本机不需要做任何事**：pending_export 不占闸，本机采样与协作派发照跑（★M4：' +
                '旧模型里「已翻离线 ⇒ 本机停采」那条腿已退役）——要包就点矩阵行内的「导出任务包」。'
              : '；③ 强制解除接管（矩阵行内，本机在下一轮边界恢复采样，协作派发立即恢复）。') +
            `判据只用已有事实：${silent ? '导包意向时刻（pending_export.at）' : '最近进度/产物时刻'} 超阈值；` +
            (leaseNote ? ` ${leaseNote} ` : ' ') +
            '修好后（重连 / 导入 / 解除）告警自动消失。',
          role: 'alert',
          actions: [
            ...(silent
              ? []
              : [
                  {
                    kind: 'resume' as const,
                    label: '强制解除接管',
                    act: 'releaseCourseHold',
                    body: { course: s.course },
                    title:
                      '立撤租墓碑 + 清 hold：本机下一轮恢复采样，协作派发立即恢复（新盘可当场 claim）',
                    primary: true,
                  },
                ]),
            { kind: 'ack', label: '知道了', ackKey },
          ],
        },
        s.course,
      ),
    )
  }
  return out
}

/** 课程热加载被拒（红条）：语料身份改动不得 mid-run 破坏血缘。 */
function courseEditAlerts(input: AlertInput): AlertItem[] {
  const edit = input.courseEdit
  if (!edit || edit.verdict !== 'rejected') return []
  // 事件身份 = 这条 course_edit 的时刻（同一字段集再次被拒是新事件，`at` 变）。
  const ackKey = alertAckKey('course-edit', input.viewing, edit.at || edit.fields.join(','))
  if (input.acks.includes(ackKey)) return []
  return [
    withCopy(
      {
        id: 'course-edit-rejected',
        severity: 'err',
        icon: '⚠',
        title: `课程文件含语料身份改动（${edit.fields.join('、') || '未识别字段'}）——热加载已拒绝`,
        detail:
          '沿用启动配置继续训练，编辑内容不进云端 payload。要应用请派生新关卡/新课程' +
          '（D14 语料血缘不可 mid-run 破坏）；改回原文件后自动解除。',
        role: 'alert',
        actions: [{ kind: 'ack', label: '知道了', ackKey }],
      },
      input.viewing,
    ),
  ]
}

/** 课程配置**不可开课**（红条，第 8 类；2026-10-05 事故，plan/course-startup-recover §3.3）。
 *
 *  事故形状：一门课因课程文件自相矛盾（`kickstart_init>0` ∧ `kickstart_ref=false`）被 trainer
 *  整课跳过，而只读视图报「无外部等待，下一步 precollect_join」——人对着不动的界面干等。
 *  本条的措辞只说我**判据面**的事实（「课程配置不可开课」），**不**写死 serve 侧动作
 *  （评审 F4：在跑的课被改了文件也会红，trainer 没起时也会红——那是判据，不是「已跳过」）。
 *
 *  可见范围 = **当前课**（有恢复动作且会被自己修好的配置解除，与停机横幅同族；切课不弹）。
 *  ack 身份 = reason 原文（不是 mtime）：改了但没修好 ⇒ 新事件 ⇒ 再弹一次（§3.3）。
 *  恢复动作不提供：那是「改课程文件」（代码编辑），控制台没有也不该有那个按钮（§2.2）——
 *  但必须给出「改完怎么生效」（改好会自动重试开跑，不需重启 trainer），否则人会以为红条在说谎。 */
function courseStartupAlerts(input: AlertInput): AlertItem[] {
  if (!input.viewing) return []
  const row = (input.loopQueueRows ?? []).find((r) => r.course === input.viewing)
  if (!row || row.openable.ok) return []
  const reason = row.openable.reason.trim() || row.waiting.text.trim()
  const ackKey = alertAckKey('course-startup', row.course, reason)
  if (input.acks.includes(ackKey)) return []
  const firstLine = reason.split('\n')[0]?.trim() || '原因不可得（读面缺 reason）'
  return [
    withCopy(
      {
        id: `course-startup-${row.course}`,
        severity: 'err',
        icon: '⚠',
        title: `${row.course} 课程配置不可开课：${firstLine}`,
        detail:
          `${reason}\n` +
          `课程文件：nn-training/curricula/${row.course}.jsonc（不带行号——行号会随编辑漂移）。` +
          '这是课程文件配置问题——配置不可开课（trainer 在跑时会整课跳过；正在跑的课不受影响）。' +
          '改好课程文件后会自动重试开跑，不需要重启 trainer。',
        role: 'alert',
        actions: [{ kind: 'ack', label: '知道了', ackKey }],
      },
      row.course,
    ),
  ]
}

/** 只读提示（蓝条 info，可关闭）：它不是事件，是这段会话的属性。
 *
 *  常驻职责已由侧栏 `tc-lock` 锁徽标承担（P0），这条只负责「第一次进来时告诉你能做什么」，
 *  关掉后不再出现；作为 info 排在错误之后——从前它和红横幅同权重堆在一起。 */
function readOnlyAlerts(input: AlertInput): AlertItem[] {
  if (!input.readOnly || input.roDismissed) return []
  return [
    withCopy(
      {
        id: 'read-only',
        severity: 'info',
        icon: '🔒',
        title: '只读模式：启停组件、冒烟、模式开关与节点编辑仅在本机 localhost 打开控制台时可用',
        detail:
          '可查看任意课程/日志/节点统计。动作按钮仍可点击，执行时会被服务端拒绝并提示' +
          '（只读是动作边界，不是把按钮涂灰）。',
        role: 'status',
        actions: [
          {
            kind: 'ack',
            label: '关闭只读提示',
            // **会话级，保留原固定键**（不是事件身份）：它关的是「这段会话的提示」，不是某个事件。
            // 事件类键走 `alertAckKey`；这张表由 app 的 `onAck` 分派（见 plan §4.3）。
            ackKey: 'ro-banner-dismissed',
            title: '关闭后不再显示（侧栏常驻 🔒 徽标不受影响）',
          },
        ],
      },
      '',
    ),
  ]
}

/** 坞的表头文案（折叠时给「还有 N 条」）。 */
export function alertDockSummary(total: number, hidden: number): string {
  return hidden > 0 ? `${hidden} 条未展开（共 ${total} 条）` : ''
}
