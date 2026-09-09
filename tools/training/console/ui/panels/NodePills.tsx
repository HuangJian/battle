/** NodePills.tsx — 节点 pill 行（一屏行）：在线 pill 一眼扫完；离线/停用默认折叠成计数 pill，
 *  点击展开（用户指令）；点在线 pill 就地编辑并发/启用/冒烟。完整统计进抽屉（节点统计 ›）。 */

import { useState } from 'preact/hooks'
import type { NodeLocalView, NodeView } from '../../../ui/view'
import { Toggle } from '../../../ui/components/Toggle'

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

export function NodePills({ nodes, local, onAction, onMore, readOnly }: NodePillsProps) {
  const [showOff, setShowOff] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const online = nodes.filter((n) => n.enabled && n.online === true)
  const offline = nodes.filter((n) => n.enabled && n.online === false)
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
  const roCls = readOnly ? ' tc-npill--ro' : ''
  if (off) {
    return (
      <span
        className={`tc-npill tc-npill--off tc-npill--dead${roCls}`}
        role={readOnly ? undefined : 'button'}
        tabIndex={readOnly ? undefined : 0}
        aria-label={`${n.id}，${n.enabled ? '离线' : '停用'}${readOnly ? '（只读）' : '，点击编辑'}`}
        title={readOnly ? RO_TITLE : undefined}
        onClick={readOnly ? undefined : onEdit}
      >
        <span className="tc-dot tc-dot--dead" />
        <b>{n.id}</b>
        <span className="v">{n.enabled ? '离线' : '停用'}</span>
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
    </span>
  )
}
