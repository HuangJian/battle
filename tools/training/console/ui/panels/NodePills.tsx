/** NodePills.tsx — 节点 pill 行（一屏行）：在线/慢/离线 pill 一眼扫完（慢与离线不折叠，
 *  用户指令 2026-09-11 + 本批：慢节点和离线节点不要自动折叠）；仅停用默认折叠成计数 pill。
 *  启停直接是 pill 上的 toggle 开关。
 *  状态三分（用户指令：慢节点别标「离线」、停用别用红点）：
 *    · 在线   — 绿点 ✓N（ping 200）
 *    · 慢     — 琥珀点「慢」（ping 失败但近期仍在成功结算：算力受限，非掉线）
 *    · 离线   — 红点「离线」（ping 失败且近期无结算 = 真掉线）
 *    · 停用   — 灰点「停用」（rl-config enabled=false）— 仍默认折叠
 *  点在线/慢 pill 就地编辑并发；冒烟与并发编辑仅本机（局域网只读无点击语义）。 */

import { useState } from 'preact/hooks'
import type { NodeLocalView, NodeView } from '../../../ui/view'
import { Switch } from '../../../ui/components/Switch'

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

/** 停用折叠 pill 的计数文案。 */
function disabledSummary(disabled: number): string {
  return `停用 ${disabled}`
}

export function NodePills({ nodes, local, onAction, onMore, readOnly }: NodePillsProps) {
  const [showDisabled, setShowDisabled] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const online = nodes.filter((n) => n.enabled && n.online === true)
  const slow = nodes.filter((n) => n.enabled && n.online === false && n.slow)
  const offline = nodes.filter((n) => n.enabled && n.online === false && !n.slow)
  const disabled = nodes.filter((n) => !n.enabled)

  return (
    <div className="tc-nodes" aria-label="节点">
      <span className="lbl">节点</span>
      {local ? (
        <span
          className="tc-npill tc-npill--local"
          title={
            local.slots > 0
              ? `本机直跑 · ${local.slots} 槽 · 上轮贡献 ${local.lastContrib >= 0 ? local.lastContrib : '—'}`
              : `本机直跑未启用（rl-config rl.local_slots = 0）· 上轮贡献 ${local.lastContrib >= 0 ? local.lastContrib : '—'}`
          }
        >
          {/* slots=0 = 直跑未启用：灰点（非绿），与「运行中」语义区分 */}
          <span className={`tc-dot ${local.slots > 0 ? 'tc-dot--on' : 'tc-dot--empty'}`} />
          <b>local</b>
          <span className="v">{local.slots}槽</span>
          <span className="tc-npill__contrib">
            {local.lastContrib > 0 ? (
              local.lastContrib
            ) : local.lastContrib === 0 ? (
              <span className="tc-muted">0</span>
            ) : (
              '—'
            )}
          </span>
        </span>
      ) : null}
      {online.map((n) => (
        <NodeEditPill
          key={n.id}
          n={n}
          editing={editing === n.id}
          draft={draft}
          readOnly={readOnly}
          onEdit={() => {
            if (readOnly) return
            setEditing(n.id)
            setDraft(String(n.concurrency))
            setShowDisabled(false)
          }}
          onDraft={(v) => setDraft(v)}
          onSave={() => {
            const num = Number(draft)
            if (Number.isInteger(num) && num >= 1 && num <= 64) {
              onAction('setNodeConcurrency', { id: n.id, concurrency: num })
            }
            setEditing(null)
          }}
          onToggle={(v) => onAction('setNodeEnabled', { id: n.id, enabled: v })}
          onSmoke={() => onAction('nodeSmoke', { id: n.id })}
        />
      ))}
      {/* 慢/离线始终展开（用户指令：不要自动折叠）；仅停用可折叠。 */}
      {[...slow, ...offline].map((n) => (
        <NodeEditPill
          key={n.id}
          n={n}
          editing={editing === n.id}
          draft={draft}
          off
          readOnly={readOnly}
          onEdit={() => {
            if (readOnly) return
            setEditing(n.id)
            setDraft(String(n.concurrency))
          }}
          onDraft={(v) => setDraft(v)}
          onSave={() => {
            const num = Number(draft)
            if (Number.isInteger(num) && num >= 1 && num <= 64) {
              onAction('setNodeConcurrency', { id: n.id, concurrency: num })
            }
            setEditing(null)
          }}
          onToggle={(v) => onAction('setNodeEnabled', { id: n.id, enabled: v })}
          onSmoke={() => onAction('nodeSmoke', { id: n.id })}
        />
      ))}
      {disabled.length > 0 ? (
        <button
          type="button"
          className="tc-npill tc-npill--collapse"
          aria-expanded={showDisabled}
          onClick={() => setShowDisabled((v) => !v)}
        >
          {showDisabled ? '▾' : '▸'} {disabledSummary(disabled.length)}
        </button>
      ) : null}
      {showDisabled
        ? disabled.map((n) => (
            <NodeEditPill
              key={n.id}
              n={n}
              editing={editing === n.id}
              draft={draft}
              off
              readOnly={readOnly}
              onEdit={() => {
                if (readOnly) return
                setEditing(n.id)
                setDraft(String(n.concurrency))
              }}
              onDraft={(v) => setDraft(v)}
              onSave={() => {
                const num = Number(draft)
                if (Number.isInteger(num) && num >= 1 && num <= 64) {
                  onAction('setNodeConcurrency', { id: n.id, concurrency: num })
                }
                setEditing(null)
              }}
              onToggle={(v) => onAction('setNodeEnabled', { id: n.id, enabled: v })}
              onSmoke={() => onAction('nodeSmoke', { id: n.id })}
            />
          ))
        : null}
      <button type="button" className="more" onClick={onMore}>
        节点统计 ›
      </button>
    </div>
  )
}

interface NodeEditPillProps {
  n: NodeView
  editing: boolean
  draft: string
  off?: boolean
  readOnly?: boolean
  onEdit: () => void
  onDraft: (v: string) => void
  onSave: () => void
  onToggle: (v: boolean) => void
  onSmoke: () => void
}

export { NodeEditPill }

/** 非 enabled 节点的折叠态标签（停用 ≠ 离线，颜色+文案双区分）。 */
function offLabel(n: NodeView): string {
  if (!n.enabled) return '停用'
  return n.slow ? '慢' : '离线'
}

/** 停用/慢节点 pill 的 modifier：停用=灰字（--disabled），慢=琥珀字（--slow，仍在贡献）。 */
function offPillCls(n: NodeView): string {
  if (n.enabled) return n.slow ? ' tc-npill--slow' : ''
  return ' tc-npill--disabled'
}

/** 非 enabled 节点的状态点样式：停用=灰（不可用），慢=琥珀（可用但慢），离线=红（掉线）。 */
function offDotCls(n: NodeView): string {
  if (!n.enabled) return 'tc-dot tc-dot--empty'
  return n.slow ? 'tc-dot tc-dot--warn' : 'tc-dot tc-dot--dead'
}

function NodeEditPill({
  n,
  editing,
  draft,
  off,
  readOnly,
  onEdit,
  onDraft,
  onSave,
  onToggle,
  onSmoke,
}: NodeEditPillProps) {
  // 只读模式：编辑态永不进入（onEdit 已挡）；无 role/onClick/tabIndex，纯展示 + 悬停提示。
  if (editing && !readOnly) {
    return (
      <span className={`tc-npill tc-npill__edit${off ? ' tc-npill--off' : ''}`}>
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
  if (off) {
    return (
      <span
        className={`tc-npill tc-npill--off tc-npill--dead${offPillCls(n)}${roCls}`}
        role={readOnly ? undefined : 'button'}
        tabIndex={readOnly ? undefined : 0}
        aria-label={`${n.id}，${offLabel(n)}${readOnly ? '（只读）' : '，点击编辑'}`}
        title={
          readOnly
            ? RO_TITLE
            : n.slow
              ? `节点响应慢（近期仍在成功结算，上轮贡献 ${n.lastContrib >= 0 ? n.lastContrib : '—'}）——ping 超时 ≠ 掉线；点击编辑`
              : undefined
        }
        onClick={readOnly ? undefined : onEdit}
      >
        <span className={offDotCls(n)} />
        <b>{n.id}</b>
        <span className="v">{offLabel(n)}</span>
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
  return (
    <span
      className={`tc-npill${roCls}`}
      role={readOnly ? undefined : 'button'}
      tabIndex={readOnly ? undefined : 0}
      aria-label={
        readOnly
          ? `${n.id} 在线，并发 ${n.concurrency}（只读）`
          : `${n.id} 在线，并发 ${n.concurrency}，上轮贡献 ${n.lastContrib >= 0 ? n.lastContrib : '—'}，点击编辑`
      }
      title={readOnly ? RO_TITLE : undefined}
      onClick={readOnly ? undefined : onEdit}
    >
      <span className="tc-dot tc-dot--on" />
      <b>{n.id}</b>
      <span className="v">✓{n.concurrency}</span>
      <span className="tc-npill__contrib" title="上一轮贡献数">
        {n.lastContrib > 0 ? (
          n.lastContrib
        ) : n.lastContrib === 0 ? (
          <span className="tc-muted">0</span>
        ) : (
          '—'
        )}
      </span>
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
