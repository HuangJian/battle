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
 */

import type { CloudHaltView, LoopComplete } from './console-types'
import { fmtTs } from './format'
import { cloudHaltAckKey, visibleCloudHalts } from './interaction'

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
  /** `kind='ack'`：已读键（按事件身份，新事件是新键——见 `cloudHaltAckKey`）。 */
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
  /** 训练正常完成停车（`ConsoleStateView.loopComplete` 原样传入）。 */
  loopComplete?: LoopComplete | null
  ppoQueueStall?: { jobId: string; waitedSec: number; it: number | null } | null
  courseEdit?: { verdict: string; fields: string[] } | null
  readOnly: boolean
  /** 只读提示是否已被关掉（写盘的状态由调用方给）。 */
  roDismissed: boolean
  /** 现刻（`Date.now()`）——相对时间/时刻文案要它，传进来才能单测。 */
  now: number
}

/** 全部条目（未排序；排序由 `sortAlerts` / `alertDockSplit` 负责）。 */
export function buildAlerts(input: AlertInput): AlertItem[] {
  return [
    ...cloudHaltAlerts(input),
    ...loopCompleteAlerts(input),
    ...ppoStallAlerts(input),
    ...courseEditAlerts(input),
    ...readOnlyAlerts(input),
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
      out.push({
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
      })
      continue
    }
    // recovered：灰条留痕（历史）
    const ackKey = cloudHaltAckKey('recovered', courseName, h.clearedAt ?? '')
    if (input.acks.includes(ackKey)) continue
    out.push({
      id: `rec-${courseName}`,
      severity: 'history',
      icon: '✓',
      title: `${who}曾停机（${h.reason}）· 已恢复`,
      detail:
        `已恢复（${h.clearReason ?? '手动恢复'}，${fmtTs(new Date(h.clearedAt ?? '').getTime(), input.now)}）；` +
        '停机期间停不掉的云机继续工作，未闲置浪费。',
      role: 'status',
      actions: [{ kind: 'ack', label: '知道了', ackKey }],
    })
  }
  return out
}

/** 训练正常完成并停车等重启（灰条留痕——它不是故障，是设计内停车）。 */
function loopCompleteAlerts(input: AlertInput): AlertItem[] {
  if (!input.loopComplete) return []
  return [
    {
      id: 'loop-complete',
      severity: 'history',
      icon: '✅',
      title: `训练已完成（${input.loopComplete.reason}）`,
      detail:
        '本地已停止采集，云机已停机省配额，进程停车等待重启。改大 iters 后经「停止→启动」继续。',
      role: 'status',
      actions: [],
    },
  ]
}

/** PPO 任务排队超时（红条）：>5min 无 worker 领取 ⇒ 云端 worker 可能断连。 */
function ppoStallAlerts(input: AlertInput): AlertItem[] {
  const stall = input.ppoQueueStall
  if (!stall) return []
  const waited = `${Math.floor(stall.waitedSec / 60)} 分${stall.waitedSec % 60} 秒`
  return [
    {
      id: 'ppo-queue-stall',
      severity: 'err',
      icon: '⚠',
      title: `PPO 任务排队超时：已等待 ${waited} 仍无 worker 领取`,
      detail:
        `job ${stall.it != null ? `it${stall.it}` : stall.jobId.slice(0, 12)}` +
        '——云端 worker 可能断连或未在轮询 hub。检查 Colab/Kaggle worker 日志与 hub 是否在线。',
      role: 'alert',
      actions: [],
    },
  ]
}

/** 课程热加载被拒（红条）：语料身份改动不得 mid-run 破坏血缘。 */
function courseEditAlerts(input: AlertInput): AlertItem[] {
  const edit = input.courseEdit
  if (!edit || edit.verdict !== 'rejected') return []
  return [
    {
      id: 'course-edit-rejected',
      severity: 'err',
      icon: '⚠',
      title: `课程文件含语料身份改动（${edit.fields.join('、') || '未识别字段'}）——热加载已拒绝`,
      detail:
        '沿用启动配置继续训练，编辑内容不进云端 payload。要应用请派生新关卡/新课程' +
        '（D14 语料血缘不可 mid-run 破坏）；改回原文件后自动解除。',
      role: 'alert',
      actions: [],
    },
  ]
}

/** 只读提示（蓝条 info，可关闭）：它不是事件，是这段会话的属性。
 *
 *  常驻职责已由侧栏 `tc-lock` 锁徽标承担（P0），这条只负责「第一次进来时告诉你能做什么」，
 *  关掉后不再出现；作为 info 排在错误之后——从前它和红横幅同权重堆在一起。 */
function readOnlyAlerts(input: AlertInput): AlertItem[] {
  if (!input.readOnly || input.roDismissed) return []
  return [
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
          // 不走 `cloudHaltAckKey`：它不是事件身份，是**会话级**的一次性已读（原来写的是
          // `TC_RO_BANNER_DISMISSED` 这个固定键）。
          ackKey: 'ro-banner-dismissed',
          title: '关闭后不再显示（侧栏常驻 🔒 徽标不受影响）',
        },
      ],
    },
  ]
}

/** 坞的表头文案（折叠时给「还有 N 条」）。 */
export function alertDockSummary(total: number, hidden: number): string {
  return hidden > 0 ? `${hidden} 条未展开（共 ${total} 条）` : ''
}
