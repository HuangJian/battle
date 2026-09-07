/** NodePills.tsx — 节点 pill 行（一屏行）：在线 pill 一眼扫完；离线/停用默认折叠成计数 pill，
 *  点击展开（用户指令）；点在线 pill 就地编辑并发/启用/冒烟。完整统计进抽屉（节点统计 ›）。 */

import { useState } from 'preact/hooks'
import type { NodeView } from '../../../ui/view'
import { Toggle } from '../../../ui/components/Toggle'

export interface NodePillsProps {
  nodes: NodeView[]
  onAction: (act: string, body: Record<string, unknown>) => void
  onMore: () => void
}

export function NodePills({ nodes, onAction, onMore }: NodePillsProps) {
  const [showOff, setShowOff] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const online = nodes.filter((n) => n.enabled && n.online === true)
  const offline = nodes.filter((n) => n.enabled && n.online === false)
  const disabled = nodes.filter((n) => !n.enabled)

  return (
    <div className="tc-nodes" aria-label="节点">
      <span className="lbl">节点</span>
      {online.map((n) => (
        <NodeEditPill
          key={n.id}
          n={n}
          editing={editing === n.id}
          draft={draft}
          onEdit={() => {
            setEditing(n.id)
            setDraft(String(n.concurrency))
            setShowOff(false)
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
      {offline.length + disabled.length > 0 ? (
        <button
          type="button"
          className="tc-npill tc-npill--collapse"
          aria-expanded={showOff}
          onClick={() => setShowOff((v) => !v)}
        >
          {showOff ? '▾' : '▸'} 离线 {offline.length} · 停用 {disabled.length}
        </button>
      ) : null}
      {showOff
        ? [...offline, ...disabled].map((n) => (
            <NodeEditPill
              key={n.id}
              n={n}
              editing={editing === n.id}
              draft={draft}
              off
              onEdit={() => {
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
  onEdit: () => void
  onDraft: (v: string) => void
  onSave: () => void
  onToggle: (v: boolean) => void
  onSmoke: () => void
}

function NodeEditPill({
  n,
  editing,
  draft,
  off,
  onEdit,
  onDraft,
  onSave,
  onToggle,
  onSmoke,
}: NodeEditPillProps) {
  if (editing) {
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
        <Toggle label={n.enabled ? '启用' : '停用'} checked={n.enabled} onChange={onToggle} />
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
  if (off) {
    return (
      <span
        className="tc-npill tc-npill--off tc-npill--dead"
        role="button"
        tabIndex={0}
        aria-label={`${n.id}，${n.enabled ? '离线' : '停用'}，点击编辑`}
        onClick={onEdit}
      >
        <span className="tc-dot tc-dot--dead" />
        <b>{n.id}</b>
        <span className="v">{n.enabled ? '离线' : '停用'}</span>
      </span>
    )
  }
  return (
    <span
      className="tc-npill"
      role="button"
      tabIndex={0}
      aria-label={`${n.id} 在线，并发 ${n.concurrency}，点击编辑`}
      onClick={onEdit}
    >
      <span className="tc-dot tc-dot--on" />
      <b>{n.id}</b>
      <span className="v">✓{n.concurrency}</span>
    </span>
  )
}
