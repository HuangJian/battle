/** NodePills.tsx — 节点行（一屏行）：健康/缓慢/离线一眼扫完（缓慢与离线不折叠，用户指令
 *  2026-09-11：慢节点和离线节点不要自动折叠）；仅**停用**默认折叠成计数行。
 *
 *  2026-09-20（docs/dashboard-redesign.md P1）：行结构迁移到 `StatusRow` 原语。
 *  交互同时改进一处：原来「点整行 → chip 换成编辑表单」会让行**在排版里换掉形状**
 *  （宽度跳变、开关位置移动）；现在改为**展开行下方详情**（`aria-expanded` 语义不变），
 *  行本身始终是同一行，开关始终在同一位置。
 *
 *  状态三分（用户指令 2026-09-20：健康度**只由最近完成轮的贡献数**判定，不再看 ping）：
 *    · 健康   — 绿点 ●（贡献 ≥ 节点并发数）
 *    · 缓慢   — 琥珀菱形 ◆（0 < 贡献 < 并发数）
 *    · 离线   — 红方 ■（贡献 0；无池数据 `—` 同判离线）
 *    · 停用   — 灰环 ○「停用」（rl-config enabled=false）— 仍默认折叠
 *
 *  **状态不写字**（用户指令 2026-09-20）：「健康/缓慢/离线」三个词都不上屏——色 + 形 + 行级
 *  修饰（`.tc-row--slow`）已经是三重编码，行里再写一个字只是重复。四档**一视同仁**：行内
 *  只剩该行的两个数（并发 + 最近完成轮贡献），判据一律归悬停（点 title + 元信息 title）。
 *  （中途曾让离线的行内改写判据句，用户 2026-09-20 明确收回：行内只留数。）
 *  贡献数取**最近完成轮**（`server/pool-history` 以训练账本的 `iteration` 事件为完成水位）：
 *  进行中那一轮的半截计数不算数——否则先交活的节点看着健康、还没轮到的看着掉线，而
 *  「这台机器上一轮到底交没交活」才是可用性的直接事实（ping 只是**可达性**）。
 *  本机直跑（local）同口径（它也是一个 rollout 执行面）；`rl.local_slots = 0`（直跑未启用）时
 *  并入「停用」折叠桶，不再占行内位置。
 *  点行就地展开并发编辑；启停是行上的 toggle；冒烟与并发编辑仅本机（局域网只读无点击语义）。
 */

import { useState } from 'preact/hooks'
import { nodeHealth, type NodeLocalView, type NodeView } from '../../view'
import { Switch } from '../../components/Switch'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusRow } from '../../components/StatusRow'
import type { StatusTone } from '../../components/StatusDot'

export interface NodePillsProps {
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：只读展示；无池数据/无槽位时缺省）。 */
  local?: NodeLocalView | null
  onAction: (act: string, body: Record<string, unknown>) => void
  onMore: () => void
  /** 局域网只读视图：行无点击语义（编辑/启用/冒烟仅本机），悬停提示说明。 */
  readOnly?: boolean
}

/** 只读视图的节点行提示。 */
const RO_TITLE = '只读模式：节点编辑/冒烟仅限本机 localhost'

/** 停用折叠行的计数文案。 */
export function disabledSummary(disabled: number): string {
  return `停用 ${disabled}`
}

/** 行的领域状态（2026-09-20 用户指令：**只由最近完成轮贡献 vs 并发数**判定，ping 出局）。 */
type RowState = 'healthy' | 'slow' | 'offline' | 'disabled'

function rowState(n: NodeView): RowState {
  if (!n.enabled) return 'disabled'
  return nodeHealth(n.lastContrib, n.concurrency)
}

/** 领域状态 → 语义档（原语只统一呈现，多对一映射留在面板）。 */
const STATE_TONE: Record<RowState, StatusTone> = {
  healthy: 'ok',
  slow: 'warn',
  offline: 'err',
  disabled: 'off',
}

/** 非健康态的行级 modifier（三重区分里的行级一重：颜色 + 形状 + 文案）。 */
const STATE_CLS: Record<RowState, string> = {
  healthy: '',
  slow: 'tc-row--slow',
  offline: '',
  disabled: 'tc-row--off',
}

/** 「停用」是唯一上屏的状态词：它不是健康度（是 `enabled=false` 的**配置事实**），
 *  且停用行不出并发数——词与数字列是同一个位置，必须有一个说法。 */
const DISABLED_WORD = '停用'

/** 判据原文（悬停解释 = 把「贡献 vs 并发」当面写出来，颜色不是唯一信息载体）。 */
function judgeText(state: RowState, contrib: number, concurrency: number): string {
  const c = `最近完成轮贡献 ${contribNum(contrib)}`
  if (state === 'slow') return `缓慢：${c} < 并发 ${concurrency}`
  if (state === 'offline') return offlineJudge(contrib, concurrency)
  if (state === 'healthy') return `健康：${c} ≥ 并发 ${concurrency}`
  return '停用（rl-config enabled = false）：不参与派发，也不探活。点开关即启用'
}

/** 贡献数（-1 = 无池数据，不冒充 0）。 */
function contribNum(v: number): string {
  return v > 0 ? String(v) : v === 0 ? '0' : '—'
}

/** 行内元信息文案：**只有那个数**（用户 2026-09-20：「不需要显示上轮字样，hover 时提示就好」）。
 *
 *  为什么删的是字样而不是整个数：行里的状态词（健康/缓慢/离线）已经由「贡献 vs 并发」判出来了，
 *  而**交了多少**仍是一个可区分的事实（缓慢的 6 与 1 是两回事）——数留、话去，口径归悬停。 */
function contribText(n: { lastContrib: number }): string {
  return contribNum(n.lastContrib)
}

/** 贡献数的口径说明（唯一来源：最近**完成**轮，rollout + eval 合计）。
 *
 *  行内不再写「上轮」⇒ 这句悬得把**数的含义 + 单位 + 缺失语义**全说完（悬停是它唯一的释义载体）。 */
function contribTitle(v: number): string {
  return `最近完成轮贡献 ${contribNum(v)} 局（rollout + eval 合计；进行中那一轮不计；— = 无池数据）`
}

/** 离线判据（**只进悬停**，用户 2026-09-20：行内不写「离线：…」）：
 *  「零交活」与「从来没池数据」是两回事（运维的下一步动作不同），所以两句文案分开 ——
 *  但它们始终只出现在悬停里（点 title + 元信息 title），行内那两格永远只是数。 */
function offlineJudge(contrib: number, need: number): string {
  if (contrib < 0) return '离线：无池数据（还没结算过这一轮）'
  return `离线：最近完成轮贡献 ${contrib} 局 < 并发 ${need}`
}

/** 单行节点（健康/缓慢/离线/停用共用；展开 = 并发编辑）。
 *
 *  导出理由与旧 `NodeEditPill` 同：行级断言（状态点形状/文案/开关在场）必须能单独渲染，
 *  否则每个用例都要先绕过「停用行默认折叠」才能碰到它。 */
export function NodeRow({
  n,
  open,
  draft,
  readOnly,
  onToggle,
  onDraft,
  onSave,
  onToggleEnabled,
  onSmoke,
}: {
  n: NodeView
  /** 展开态（并发编辑详情）。 */
  open: boolean
  draft: string
  readOnly?: boolean
  onToggle: () => void
  onDraft: (v: string) => void
  onSave: () => void
  onToggleEnabled: (v: boolean) => void
  onSmoke: () => void
}) {
  const state = rowState(n)
  const disabled = state === 'disabled'
  const judge = judgeText(state, n.lastContrib, n.concurrency)
  const toggler = (
    <Switch
      label={`${n.enabled ? '停用' : '启用'} ${n.id}`}
      checked={n.enabled}
      disabled={n.busy}
      onChange={onToggleEnabled}
    />
  )
  return (
    <StatusRow
      tone={STATE_TONE[state]}
      dotTitle={judge}
      name={n.id}
      // 并发数恒出（用户的判据是「贡献 vs 并发」，少了并发数就核对不了）；
      // 去掉旧的前缀 ✓——它把「并发数」误读成「在线数」。停用行改出状态词。
      value={disabled ? DISABLED_WORD : String(n.concurrency)}
      valueTitle={disabled ? judge : `并发数 ${n.concurrency}（判据：最近完成轮贡献 ≥ 它 ⇒ 健康）`}
      // 状态不写字（见文件头注）：四档元信息都只是那个数（离线也一样），判据全归悬停。
      meta={[
        { text: contribText(n), title: state === 'offline' ? judge : contribTitle(n.lastContrib) },
      ]}
      onToggle={onToggle}
      expanded={open}
      readOnly={readOnly}
      roTitle={readOnly ? RO_TITLE : undefined}
      ariaLabel={
        readOnly
          ? `${n.id}，${disabled ? DISABLED_WORD : `并发 ${n.concurrency}，${judge}`}（只读）`
          : `${n.id}，${disabled ? DISABLED_WORD : `并发 ${n.concurrency}，${judge}`}，点击展开并发编辑`
      }
      className={STATE_CLS[state]}
      actions={toggler}
      detail={
        <div className="tc-nodeedit">
          <label>
            <span className="tc-muted tc-small">并发</span>
            <input
              type="number"
              min={1}
              max={64}
              aria-label={`${n.id} 并发数`}
              value={draft}
              onInput={(e) => onDraft((e.currentTarget as HTMLInputElement).value)}
            />
          </label>
          <button type="button" className="tc-btn tc-btn--sm" disabled={n.busy} onClick={onSave}>
            保存
          </button>
          <span className="tc-muted tc-small">1–64；保存写 rl-config，训练栈重启后生效</span>
          {n.enabled ? (
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              disabled={n.busy}
              aria-label={`冒烟 ${n.id}`}
              title="对该节点跑一次冒烟（仅本机）"
              onClick={onSmoke}
            >
              冒烟
            </button>
          ) : null}
        </div>
      }
    />
  )
}

/** 本机直跑行（只读：无点击/编辑语义——启用与否由 rl-config `rl.local_slots` 决定）。
 *  `off` = 直跑未启用（slots = 0），并入「停用」折叠桶。 */
export function LocalRow({ local, off }: { local: NodeLocalView; off?: boolean }) {
  const state: RowState = off ? 'disabled' : nodeHealth(local.lastContrib, local.slots)
  const judge = judgeText(state, local.lastContrib, local.slots)
  return (
    <StatusRow
      tone={STATE_TONE[state]}
      dotTitle={off ? '本机直跑未启用（rl.local_slots = 0）' : judge}
      name="local"
      // 槽位数恒出（§361⑤ 的展示契约：本机直跑行只读展示槽位 + 贡献数）
      value={off ? DISABLED_WORD : `${local.slots}槽`}
      valueTitle={
        off
          ? '本机直跑未启用（rl-config rl.local_slots = 0）'
          : `本机直跑槽数（rl.local_slots；判据：最近完成轮贡献 ≥ 它 ⇒ 健康）`
      }
      // 状态不写字：与节点行同规（行内只有那个数，判据归悬停）
      meta={[
        {
          text: contribText(local),
          title: state === 'offline' ? judge : contribTitle(local.lastContrib),
        },
      ]}
      // 行级 modifier 与节点行同源（`tc-row--local` 是身份、`tc-row--slow/off` 是状态）
      className={`tc-row--local${STATE_CLS[state] ? ` ${STATE_CLS[state]}` : ''}`}
      ariaLabel={`本机直跑 ${local.slots} 槽，${off ? DISABLED_WORD : judge}`}
    />
  )
}

export function NodePills({ nodes, local, onAction, onMore, readOnly }: NodePillsProps) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [showDisabled, setShowDisabled] = useState(false)

  // 三桶按健康度分（缓慢/离线始终展开——它们需要被看见）。
  const healthy: NodeView[] = []
  const slow: NodeView[] = []
  const offline: NodeView[] = []
  for (const n of nodes) {
    if (!n.enabled) continue
    const h = nodeHealth(n.lastContrib, n.concurrency)
    if (h === 'healthy') healthy.push(n)
    else if (h === 'slow') slow.push(n)
    else offline.push(n)
  }
  const disabled = nodes.filter((n) => !n.enabled)
  // local：slots > 0 出在行内（同口径健康度）；slots = 0（直跑未启用）并入「停用」桶。
  const localLive = local && local.slots > 0 ? local : null
  const localOff = local != null && local.slots <= 0
  const disabledCount = disabled.length + (localOff ? 1 : 0)

  /** 行展开/收起（只读时不进入——`StatusRow` 也会挡，这里顺手不要制造无用的 draft）。 */
  const toggle = (n: NodeView): void => {
    if (readOnly) return
    if (editing === n.id) {
      setEditing(null)
      return
    }
    setEditing(n.id)
    setDraft(String(n.concurrency))
  }

  const saveConcurrency = (n: NodeView): void => {
    const num = Number(draft)
    if (Number.isInteger(num) && num >= 1 && num <= 64)
      onAction('setNodeConcurrency', { id: n.id, concurrency: num })
    setEditing(null)
  }

  const row = (n: NodeView) => (
    <NodeRow
      key={n.id}
      n={n}
      open={editing === n.id}
      draft={draft}
      readOnly={readOnly}
      onToggle={() => toggle(n)}
      onDraft={setDraft}
      onSave={() => saveConcurrency(n)}
      onToggleEnabled={(v) => onAction('setNodeEnabled', { id: n.id, enabled: v })}
      onSmoke={() => onAction('nodeSmoke', { id: n.id })}
    />
  )

  return (
    <div className="tc-nodes" aria-label="节点">
      <SectionHeader
        title="节点"
        hint="算力供给：本机直跑槽位 + 各登记节点。健康度 = 最近完成轮贡献 vs 并发数（0 → 离线 / 不足 → 缓慢 / ≥ 并发 → 健康）；缓慢与离线不折叠，仅停用可折叠"
      />
      {localLive ? <LocalRow local={localLive} /> : null}
      {healthy.map((n) => row(n))}
      {/* 缓慢/离线始终展开（用户指令：不要自动折叠）；仅停用可折叠。 */}
      {[...slow, ...offline].map((n) => row(n))}
      {disabledCount > 0 ? (
        <button
          type="button"
          className="tc-row tc-row--act tc-row--off"
          aria-expanded={showDisabled}
          aria-label={`${showDisabled ? '收起' : '展开'}停用节点`}
          title="停用（rl-config enabled=false 的节点 + 本机直跑未启用）：不参与派发也不探活，点开关即启用"
          onClick={() => setShowDisabled((v) => !v)}
        >
          <b>
            {showDisabled ? '▾' : '▸'} {disabledSummary(disabledCount)}
          </b>
        </button>
      ) : null}
      {showDisabled ? (
        <>
          {localOff && local ? <LocalRow local={local} off /> : null}
          {disabled.map((n) => row(n))}
        </>
      ) : null}
      <button type="button" className="tc-btn tc-btn--sm" onClick={onMore}>
        节点统计 ›
      </button>
    </div>
  )
}
