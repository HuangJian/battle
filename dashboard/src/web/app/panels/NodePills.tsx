/** NodePills.tsx — 节点 pill 行（一屏行）：健康/缓慢/离线 pill 一眼扫完（缓慢与离线不折叠，
 *  用户指令 2026-09-11 + 本批）；仅停用（含 local 0 槽）默认折叠成计数 pill。
 *  启停直接是 pill 上的 toggle 开关。
 *
 *  状态三分（用户指令 2026-09-20：健康度**只由最近完成轮的贡献数**判定，不再看 ping）：
 *    · 健康   — 绿点（贡献 ≥ 节点并发数）
 *    · 缓慢   — 琥珀点「缓慢」（0 < 贡献 < 并发数）
 *    · 离线   — 红点「离线」（贡献 0；无池数据 = 「—」，同判离线）
 *    · 停用   — 灰点「停用」（rl-config enabled=false）— 仍默认折叠
 *  贡献数取**最近完成轮**（`server/pool-history` 以训练账本的 iteration 事件为完成水位）：
 *  进行中那一轮的半截计数不算数——否则先交活的节点看着健康、还没轮到的看着掉线，而
 *  「这台机器上一轮到底交没交活」才是节点可用性的直接事实（ping 只是可达性）。
 *  本机直跑（local）同口径（它也是一个 rollout 执行面）；`local_slots = 0`（直跑未启用）时
 *  并入「停用」桶，不再占行内位置。
 *  点健康/缓慢 pill 就地编辑并发；冒烟与并发编辑仅本机（局域网只读无点击语义）。 */

import { useState } from 'preact/hooks'
import { nodeHealth, type NodeLocalView, type NodeView } from '../../view'
import { Switch } from '../../components/Switch'

export interface NodePillsProps {
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：只读展示；无池数据/无槽位时缺省）。 */
  local?: NodeLocalView | null
  onAction: (act: string, body: Record<string, unknown>) => void
  onMore: () => void
  /** 局域网只读视图：pill 无点击语义（编辑/启用/冒烟仅本机），悬停提示说明。 */
  readOnly?: boolean
}

/** 只读视图的节点 pill 提示。 */
const RO_TITLE = '只读模式：节点编辑/冒烟仅限本机 localhost'

/** 贡献数单元格的悬停解释（口径唯一来源：最近**完成**轮，rollout + eval 合计）。 */
const CONTRIB_HINT = '最近完成轮贡献数（rollout + eval；进行中那一轮不计）'

/** 停用折叠 pill 的计数文案。 */
function disabledSummary(disabled: number): string {
  return `停用 ${disabled}`
}

/** pill 行的状态四态：停用优先（enabled=false），否则按最近完成轮贡献判健康度。 */
type NodeState = 'healthy' | 'slow' | 'offline' | 'disabled'

function nodeState(n: NodeView): NodeState {
  if (!n.enabled) return 'disabled'
  return nodeHealth(n.lastContrib, n.concurrency)
}

/** 状态 → 点样式 / pill 修饰类 / 状态词（健康态不出词：绿点 + 并发数就已经是「满并发」的表达）。
 *
 *  并发数**恒出**（用户的判据是「贡献 vs 并发」，少了并发数这两个数就无法当场核对）：
 *  健康时它是唯一的数字列，缓慢/离线时后面再多一个状态词。停用是另一码事（没在跑），
 *  沿用旧形状——`停用` 顶替数字列。 */
function stateStyle(state: NodeState): { pill: string; dot: string; label: string } {
  if (state === 'healthy') return { pill: '', dot: 'tc-dot tc-dot--on', label: '' }
  if (state === 'slow') {
    return {
      pill: ' tc-npill--off tc-npill--dead tc-npill--slow',
      dot: 'tc-dot tc-dot--warn',
      label: '缓慢',
    }
  }
  // 离线（贡献 0）与停用共用「不点亮」的形状，靠点色（红 vs 灰）+ 文案区分。
  if (state === 'offline') {
    return { pill: ' tc-npill--off tc-npill--dead', dot: 'tc-dot tc-dot--dead', label: '离线' }
  }
  return {
    pill: ' tc-npill--off tc-npill--dead tc-npill--disabled',
    dot: 'tc-dot tc-dot--empty',
    label: '停用',
  }
}

/** 贡献数 → 纯文本（tooltip/aria 用；-1 = 无池数据）。 */
function contribText(v: number): string {
  return v > 0 ? String(v) : v === 0 ? '0' : '—'
}

/** 状态 → 悬停解释（把判据本身写出来：贡献 0 ⇒ 离线 / 不足并发 ⇒ 缓慢 / 满并发 ⇒ 健康）。 */
function healthTitle(state: NodeState, contrib: number, concurrency: number): string {
  const c = `最近完成轮贡献 ${contribText(contrib)}`
  if (state === 'slow') return `缓慢：${c} < 并发 ${concurrency}`
  if (state === 'offline') return `离线：${c}`
  if (state === 'healthy') return `健康：${c} ≥ 并发 ${concurrency}`
  return '停用（rl-config enabled = false）'
}

function stateTitle(n: NodeView): string {
  return healthTitle(nodeState(n), n.lastContrib, n.concurrency)
}

/** 最近完成轮的贡献数：>0 绿字；0 灰显（= 离线判据）；-1 无池数据 = 「—」。 */
function ContribCell({ v }: { v: number }) {
  if (v > 0) {
    return (
      <span className="tc-npill__contrib" title={CONTRIB_HINT}>
        {v}
      </span>
    )
  }
  if (v === 0) {
    return (
      <span className="tc-npill__contrib" title={`${CONTRIB_HINT}：0 ⇒ 离线`}>
        <span className="tc-muted">0</span>
      </span>
    )
  }
  return (
    <span className="tc-npill__contrib" title="无池数据：该节点还没在活跃训练流里结算过">
      —
    </span>
  )
}

export function NodePills({ nodes, local, onAction, onMore, readOnly }: NodePillsProps) {
  const [showDisabled, setShowDisabled] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  // 三桶按健康度分（缓慢/离线始终展开，用户指令：不要自动折叠）。
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

  const pillProps = (n: NodeView) => ({
    n,
    editing: editing === n.id,
    draft,
    readOnly,
    onEdit: () => {
      if (readOnly) return
      setEditing(n.id)
      setDraft(String(n.concurrency))
      setShowDisabled(false)
    },
    onDraft: setDraft,
    onSave: () => {
      const num = Number(draft)
      if (Number.isInteger(num) && num >= 1 && num <= 64) {
        onAction('setNodeConcurrency', { id: n.id, concurrency: num })
      }
      setEditing(null)
    },
    onToggle: (v: boolean) => onAction('setNodeEnabled', { id: n.id, enabled: v }),
    onSmoke: () => onAction('nodeSmoke', { id: n.id }),
  })

  return (
    <div className="tc-nodes" aria-label="节点">
      <span className="lbl">节点</span>
      {localLive ? <LocalPill local={localLive} /> : null}
      {/* 健康在前，缓慢/离线随后（始终展开）；仅停用可折叠。 */}
      {[...healthy, ...slow, ...offline].map((n) => (
        <NodeEditPill key={n.id} {...pillProps(n)} />
      ))}
      {disabledCount > 0 ? (
        <button
          type="button"
          className="tc-npill tc-npill--collapse"
          aria-expanded={showDisabled}
          onClick={() => setShowDisabled((v) => !v)}
        >
          {showDisabled ? '▾' : '▸'} {disabledSummary(disabledCount)}
        </button>
      ) : null}
      {showDisabled ? (
        <>
          {localOff && local ? <LocalPill local={local} off /> : null}
          {disabled.map((n) => (
            <NodeEditPill key={n.id} {...pillProps(n)} />
          ))}
        </>
      ) : null}
      <button type="button" className="more" onClick={onMore}>
        节点统计 ›
      </button>
    </div>
  )
}

/** 本机直跑 pill（只读：无点击/编辑语义——启用与否由 rl-config `rl.local_slots` 决定）。
 *  `off` = 直跑未启用（slots = 0），并入「停用」折叠桶。 */
export function LocalPill({ local, off }: { local: NodeLocalView; off?: boolean }) {
  if (off) {
    return (
      <span
        className="tc-npill tc-npill--local tc-npill--off tc-npill--dead tc-npill--disabled"
        aria-label="local 停用（本机直跑未启用：rl-config rl.local_slots = 0）"
        title={`本机直跑未启用（rl-config rl.local_slots = 0）· 最近完成轮贡献 ${contribText(local.lastContrib)}`}
      >
        <span className="tc-dot tc-dot--empty" />
        <b>local</b>
        <span className="v">停用</span>
      </span>
    )
  }
  const state = nodeHealth(local.lastContrib, local.slots)
  const style = stateStyle(state)
  return (
    <span
      className={`tc-npill tc-npill--local${state === 'healthy' ? '' : style.pill}`}
      aria-label={`local ${state === 'healthy' ? '健康' : style.label}，${local.slots} 槽，最近完成轮贡献 ${contribText(local.lastContrib)}`}
      title={`本机直跑 · ${local.slots} 槽 · ${healthTitle(state, local.lastContrib, local.slots)}`}
    >
      <span className={style.dot} />
      <b>local</b>
      {/* 槽位数恒出（§361⑤：本机直跑 pill 只读展示槽位 + 上轮贡献） */}
      <span className="v">{local.slots}槽</span>
      {style.label ? <span className="tc-npill__state">{style.label}</span> : null}
      <ContribCell v={local.lastContrib} />
    </span>
  )
}

interface NodeEditPillProps {
  n: NodeView
  editing: boolean
  draft: string
  readOnly?: boolean
  onEdit: () => void
  onDraft: (v: string) => void
  onSave: () => void
  onToggle: (v: boolean) => void
  onSmoke: () => void
}

export { NodeEditPill }

function NodeEditPill({
  n,
  editing,
  draft,
  readOnly,
  onEdit,
  onDraft,
  onSave,
  onToggle,
  onSmoke,
}: NodeEditPillProps) {
  const state = nodeState(n)
  const contrib = <ContribCell v={n.lastContrib} />
  // 只读模式：编辑态永不进入（onEdit 已挡）；无 role/onClick/tabIndex，纯展示 + 悬停提示。
  if (editing && !readOnly) {
    return (
      <span className={`tc-npill tc-npill__edit${state === 'healthy' ? '' : ' tc-npill--off'}`}>
        <b>{n.id}</b>
        <input
          type="number"
          min={1}
          max={64}
          aria-label={`${n.id} 并发数`}
          value={draft}
          onInput={(e) => onDraft((e.target as HTMLInputElement).value)}
        />
        <button type="button" className="tc-btn tc-btn--sm" disabled={n.busy} onClick={onSave}>
          保存
        </button>
        <Switch
          label={`${n.enabled ? '停用' : '启用'} ${n.id}`}
          checked={n.enabled}
          disabled={n.busy}
          onChange={onToggle}
        />
        <span className="tc-muted tc-small">启用</span>
        {n.enabled ? (
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            disabled={n.busy}
            aria-label={`冒烟 ${n.id}`}
            onClick={onSmoke}
          >
            冒烟
          </button>
        ) : null}
      </span>
    )
  }
  const roCls = readOnly ? ' tc-npill--ro' : ''
  if (state === 'healthy') {
    return (
      <span
        className={`tc-npill${roCls}`}
        role={readOnly ? undefined : 'button'}
        tabIndex={readOnly ? undefined : 0}
        aria-label={
          readOnly
            ? `${n.id} 健康，并发 ${n.concurrency}（只读）`
            : `${n.id} 健康，并发 ${n.concurrency}，最近完成轮贡献 ${contribText(n.lastContrib)}，点击编辑`
        }
        title={readOnly ? RO_TITLE : stateTitle(n)}
        onClick={readOnly ? undefined : onEdit}
      >
        <span className="tc-dot tc-dot--on" />
        <b>{n.id}</b>
        {/* 并发数（2026-09-20 用户指令：去掉前缀对钩——它把「并发数」误导成「在线数」） */}
        <span className="v">{n.concurrency}</span>
        {contrib}
        {/* 启停 toggle 直接放 pill 上（用户指令：直观）——点开关即切，不再进编辑态找 checkbox。
            只读模式下不禁用（与其它动作键同哲学：可点、服务端 403 + toast 提示）。 */}
        <Switch
          label={`${n.enabled ? '停用' : '启用'} ${n.id}`}
          checked={n.enabled}
          disabled={n.busy}
          onChange={onToggle}
        />
      </span>
    )
  }
  const style = stateStyle(state)
  return (
    <span
      className={`tc-npill${style.pill}${roCls}`}
      role={readOnly ? undefined : 'button'}
      tabIndex={readOnly ? undefined : 0}
      aria-label={
        state === 'disabled'
          ? `${n.id} 停用（rl-config enabled = false）${readOnly ? '（只读）' : ''}`
          : `${n.id}，${style.label}（最近完成轮贡献 ${contribText(n.lastContrib)} / 并发 ${n.concurrency}）${readOnly ? '（只读）' : '，点击编辑'}`
      }
      title={readOnly ? RO_TITLE : stateTitle(n)}
      onClick={readOnly ? undefined : onEdit}
    >
      <span className={style.dot} />
      <b>{n.id}</b>
      {/* 停用：数字列改出「停用」；其余状态并发数恒出 + 状态词（判据两个数都在场）。 */}
      <span className="v">{state === 'disabled' ? style.label : n.concurrency}</span>
      {state === 'disabled' || !style.label ? null : (
        <span className="tc-npill__state">{style.label}</span>
      )}
      {/* 贡献数照出（判据本身）：停用节点不展示——它没在跑，数字只会误导。 */}
      {state === 'disabled' ? null : contrib}
      {/* 启停 toggle 直接放 pill 上（用户指令：直观）——慢/离线/停用都可用开关启用 */}
      <Switch
        label={`${n.enabled ? '停用' : '启用'} ${n.id}`}
        checked={n.enabled}
        disabled={n.busy}
        onChange={onToggle}
      />
    </span>
  )
}
