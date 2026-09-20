/** NodePills.tsx — 节点行（一屏行）：在线/慢/离线一眼扫完（慢与离线不折叠，用户指令
 *  2026-09-11：慢节点和离线节点不要自动折叠）；仅**停用**默认折叠成计数行。
 *
 *  2026-09-20（docs/dashboard-redesign.md P1）：行结构迁移到 `StatusRow` 原语。
 *  交互同时改进一处：原来「点整行 → chip 换成编辑表单」会让行**在排版里换掉形状**
 *  （宽度跳变、开关位置移动）；现在改为**展开行下方详情**（`aria-expanded` 语义不变），
 *  行本身始终是同一行，开关始终在同一位置。
 *
 *  状态三分（用户指令：慢节点别标「离线」、停用别用红点）：
 *    · 在线   — 绿点 ●（ping 200）
 *    · 慢     — 琥珀菱形 ◆「慢」（ping 失败但近期仍在成功结算：算力受限，非掉线）
 *    · 离线   — 红方 ■「离线」（ping 失败且近期无结算 = 真掉线）
 *    · 停用   — 灰环 ○「停用」（rl-config enabled=false）— 仍默认折叠
 *  点行就地展开并发编辑；启停是行上的 toggle；冒烟与并发编辑仅本机（局域网只读无点击语义）。
 */

import { useState } from 'preact/hooks'
import type { NodeLocalView, NodeView } from '../../view'
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

/** 非 enabled 节点的语义档 + 状态词（停用 ≠ 离线，颜色/形状/文案三重区分）。 */
function offTone(n: NodeView): { tone: StatusTone; label: string; title: string; cls: string } {
  if (!n.enabled)
    return {
      tone: 'off',
      label: '停用',
      title: 'rl-config 里 enabled=false：不参与派发，也不探活。点开关即启用',
      cls: 'tc-row--off',
    }
  if (n.slow)
    return {
      tone: 'warn',
      label: '慢',
      title: `ping 超时但近期仍在成功结算（算力受限，不是掉线）——上轮贡献 ${
        n.lastContrib >= 0 ? n.lastContrib : '—'
      }`,
      cls: 'tc-row--slow',
    }
  return {
    tone: 'err',
    label: '离线',
    title: 'ping 失败且近期无成功结算 = 真掉线',
    cls: '',
  }
}

/** 上轮贡献的展示（-1 = 无池数据，不冒充 0）。 */
function contribText(n: { lastContrib: number }): string {
  return n.lastContrib > 0 ? `上轮 ${n.lastContrib}` : n.lastContrib === 0 ? '上轮 0' : '上轮 —'
}

/** 单行节点（在线/慢/离线/停用共用；展开 = 并发编辑）。
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
  const tone: StatusTone = n.enabled ? (n.online === true ? 'ok' : n.slow ? 'warn' : 'err') : 'off'
  const off = offTone(n)
  const useOff = !(n.enabled && n.online === true)
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
      tone={tone}
      dotTitle={useOff ? off.title : `在线（ping 200）· 并发 ${n.concurrency}`}
      name={n.id}
      value={useOff ? off.label : `✓${n.concurrency}`}
      valueTitle={useOff ? off.title : `并发数 ${n.concurrency}`}
      meta={[{ text: contribText(n), title: '上一轮贡献数（-1 = 无池数据）' }]}
      onToggle={onToggle}
      expanded={open}
      readOnly={readOnly}
      roTitle={readOnly ? RO_TITLE : undefined}
      ariaLabel={
        readOnly
          ? `${n.id}，${useOff ? off.label : `在线，并发 ${n.concurrency}`}（只读）`
          : `${n.id}，${useOff ? off.label : `在线，并发 ${n.concurrency}`}，${contribText(n)}，点击展开并发编辑`
      }
      className={off.cls}
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

export function NodePills({ nodes, local, onAction, onMore, readOnly }: NodePillsProps) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [showDisabled, setShowDisabled] = useState(false)

  const online = nodes.filter((n) => n.enabled && n.online === true)
  const slow = nodes.filter((n) => n.enabled && n.online === false && n.slow)
  const offline = nodes.filter((n) => n.enabled && n.online === false && !n.slow)
  const disabled = nodes.filter((n) => !n.enabled)

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
        hint="算力供给：本机直跑槽位 + 各登记节点。慢/离线不折叠（它们需要被看见），仅停用可折叠"
      />
      {local ? (
        <StatusRow
          tone={local.slots > 0 ? 'ok' : 'off'}
          dotTitle={
            local.slots > 0
              ? '本机直跑已启用（rl.local_slots > 0）'
              : '本机直跑未启用（rl.local_slots = 0）'
          }
          name="local"
          value={`${local.slots}槽`}
          valueTitle="本机直跑槽数（rl.local_slots）"
          meta={[{ text: contribText(local), title: '上一轮贡献数（-1 = 无池数据）' }]}
          className="tc-row--local"
          ariaLabel={`本机直跑 ${local.slots} 槽，${contribText(local)}`}
        />
      ) : null}
      {online.map((n) => row(n))}
      {/* 慢/离线始终展开（用户指令：不要自动折叠）；仅停用可折叠。 */}
      {[...slow, ...offline].map((n) => row(n))}
      {disabled.length > 0 ? (
        <button
          type="button"
          className="tc-row tc-row--act tc-row--off"
          aria-expanded={showDisabled}
          aria-label={`${showDisabled ? '收起' : '展开'}停用节点`}
          title="停用节点（rl-config enabled=false）：不参与派发也不探活，点开关即启用"
          onClick={() => setShowDisabled((v) => !v)}
        >
          <b>
            {showDisabled ? '▾' : '▸'} {disabledSummary(disabled.length)}
          </b>
        </button>
      ) : null}
      {showDisabled ? disabled.map((n) => row(n)) : null}
      <button type="button" className="tc-btn tc-btn--sm" onClick={onMore}>
        节点统计 ›
      </button>
    </div>
  )
}
