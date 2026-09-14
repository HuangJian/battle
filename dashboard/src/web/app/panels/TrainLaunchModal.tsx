/** TrainLaunchModal.tsx — 启动 TrainingLoop 的弹窗（工具行并入此处，用户指令）：
 *  选择 trainer 模式（Pull/Push/Local）+ rl-config 行为开关（即时写）+ 推送链路预演入口。
 *  Push 模式：必填 cloudflared endpoint + auth key（服务端 ping 通才启动并回写 rl-config）。
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { ModeView } from '../../view'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Toggle } from '../../components/Toggle'
import { TC_TRAIN_MODE, TC_TRAIN_TOGGLES } from '../../view'

export interface PushCredentials {
  endpoint: string
  authKey: string
}

export interface TrainLaunchModalProps {
  open: boolean
  modes: ModeView
  onClose: () => void
  onAction: (act: string, body: Record<string, unknown>) => void
  onLaunch: (mode: 'pull' | 'push' | 'local', push?: PushCredentials) => void
  /** 局域网只读视图：行为开关/预演/启动全部禁用（兜底——启动入口可点，弹窗内禁用以防误操作）。 */
  readOnly?: boolean
}

const TC_PUSH_ENDPOINT = 'tc.pushEndpoint'
const TC_PUSH_AUTH = 'tc.pushAuthKey'

function readLocal(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeLocal(key: string, v: string): void {
  try {
    localStorage.setItem(key, v)
  } catch {
    /* ignore */
  }
}

export function TrainLaunchModal({
  open,
  modes,
  onClose,
  onAction,
  onLaunch,
  readOnly,
}: TrainLaunchModalProps) {
  type Mode = 'pull' | 'push' | 'local'

  // trainer 模式偏好：localStorage 优先 → 服务端 modes 兜底
  const [mode, setMode] = useState<Mode>(() => {
    try {
      const v = localStorage.getItem(TC_TRAIN_MODE)
      if (v === 'pull' || v === 'push' || v === 'local') return v
    } catch {
      /* ignore */
    }
    return modes.trainerPpo
  })

  const [pushEndpoint, setPushEndpoint] = useState(() => readLocal(TC_PUSH_ENDPOINT))
  const [pushAuthKey, setPushAuthKey] = useState(() => readLocal(TC_PUSH_AUTH))
  const [pushErr, setPushErr] = useState('')

  // 行为开关偏好：localStorage 优先 → 服务端 modes 兜底
  const [toggles, setToggles] = useState<{
    stream: boolean
    doubleBuffer: boolean
    precollectEarly: boolean
  }>(() => {
    try {
      const raw = localStorage.getItem(TC_TRAIN_TOGGLES)
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, boolean>
        return {
          stream: parsed.stream == null ? modes.stream === 1 : parsed.stream,
          doubleBuffer:
            parsed.doubleBuffer == null ? modes.doubleBuffer === 1 : parsed.doubleBuffer,
          precollectEarly:
            parsed.precollectEarly == null ? modes.precollectEarly === 1 : parsed.precollectEarly,
        }
      }
    } catch {
      /* ignore */
    }
    return {
      stream: modes.stream === 1,
      doubleBuffer: modes.doubleBuffer === 1,
      precollectEarly: modes.precollectEarly === 1,
    }
  })

  // 每次变化时持久化到 localStorage（服务端写由 onAction 负责，不在此处耦合）
  const togglesRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!togglesRef.current) return
    const prev = JSON.parse(localStorage.getItem(TC_TRAIN_TOGGLES) ?? '{}') as Record<
      string,
      boolean
    > | null
    if (prev && JSON.stringify(prev) === JSON.stringify(toggles)) return
    try {
      localStorage.setItem(TC_TRAIN_TOGGLES, JSON.stringify(toggles))
    } catch {
      /* ignore */
    }
  }, [toggles])

  useEffect(() => {
    if (mode === modes.trainerPpo) return
    try {
      localStorage.setItem(TC_TRAIN_MODE, mode)
    } catch {
      /* ignore */
    }
  }, [mode])

  useEffect(() => {
    if (!open) setPushErr('')
  }, [open])

  const applyToggle = (key: string, v: boolean): void => {
    const next = { ...toggles, [key]: v }
    setToggles(next)
    onAction('setMode', { key, value: v ? '1' : '0' })
  }

  const handleLaunchClick = (): void => {
    if (mode !== 'push') {
      onLaunch(mode)
      return
    }
    const endpoint = pushEndpoint.trim()
    const authKey = pushAuthKey.trim()
    if (!endpoint || !authKey) {
      setPushErr('Push 模式必须填写 cloudflared endpoint 与 auth key')
      return
    }
    setPushErr('')
    writeLocal(TC_PUSH_ENDPOINT, endpoint)
    writeLocal(TC_PUSH_AUTH, authKey)
    onLaunch(mode, { endpoint, authKey })
  }

  if (!open) return null
  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal"
        role="dialog"
        aria-label="启动 TrainingLoop"
        onClick={(e) => e.stopPropagation()}
      >
        <h3>启动 TrainingLoop</h3>
        {readOnly ? (
          <p className="tc-banner tc-banner--ro" style={{ margin: 0 }}>
            🔒 只读模式：启动训练仅限本机 localhost 打开控制台操作。
          </p>
        ) : null}
        <div className="tc-line">
          <span className="tc-muted tc-small" style={{ minWidth: 90 }}>
            trainer 编排
          </span>
          <SegmentedControl<'pull' | 'push' | 'local'>
            value={mode}
            ariaLabel="trainer 模式"
            options={[
              { value: 'pull', label: 'Pull' },
              { value: 'push', label: 'Push' },
              { value: 'local', label: 'Local' },
            ]}
            onChange={setMode}
          />
        </div>
        {mode === 'push' ? (
          <div className="tc-push-creds" style={{ display: 'grid', gap: 8, marginTop: 4 }}>
            <label className="tc-line" style={{ display: 'grid', gap: 4 }}>
              <span className="tc-muted tc-small">
                cloudflared / worker_server endpoint（必填，启动前 ping）
              </span>
              <input
                type="url"
                className="tc-input"
                placeholder="https://xxxx.trycloudflare.com"
                value={pushEndpoint}
                disabled={readOnly}
                onChange={(e) => setPushEndpoint((e.target as HTMLInputElement).value)}
              />
            </label>
            <label className="tc-line" style={{ display: 'grid', gap: 4 }}>
              <span className="tc-muted tc-small">auth key（worker_server --token，必填）</span>
              <input
                type="password"
                className="tc-input"
                placeholder="Bearer token"
                value={pushAuthKey}
                disabled={readOnly}
                onChange={(e) => setPushAuthKey((e.target as HTMLInputElement).value)}
              />
            </label>
            {pushErr ? (
              <p className="tc-banner tc-banner--err" style={{ margin: 0 }} role="alert">
                {pushErr}
              </p>
            ) : (
              <p className="tc-muted tc-small" style={{ margin: 0 }}>
                服务端将 GET {'{url}'}/ping 校验连通性，通过后回写 rl-config.json 再启动训练。
              </p>
            )}
          </div>
        ) : null}
        <div className="tc-line tc-toggle-group" ref={togglesRef}>
          <span className="tc-muted tc-small">行为开关</span>
          <Toggle
            label="stream"
            checked={toggles.stream}
            title="rl.stream：run_rl 的 --stream；本地默认开，远程内部强制 0"
            disabled={readOnly}
            onChange={(v) => applyToggle('rl.stream', v)}
          />
          <Toggle
            label="双缓冲"
            checked={toggles.doubleBuffer}
            title="rl.double_buffer"
            disabled={readOnly}
            onChange={(v) => applyToggle('rl.double_buffer', v)}
          />
          <Toggle
            label="预采"
            checked={toggles.precollectEarly}
            title="rl.precollect_early"
            disabled={readOnly}
            onChange={(v) => applyToggle('rl.precollect_early', v)}
          />
        </div>
        <div className="tc-line">
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            title={
              readOnly
                ? '只读模式：预演仅限本机 localhost'
                : '端到端预演：本机伪 GPU 节点 echo（不跑真 PPO）'
            }
            disabled={readOnly}
            onClick={() => onAction('smokeTrain', {})}
          >
            推送链路预演（不跑 PPO）
          </button>
        </div>
        <div className="tc-modal__foot">
          <span className="sp" />
          <button type="button" className="tc-btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="tc-btn tc-btn--primary"
            aria-label={`按 ${mode} 模式启动 TrainingLoop`}
            disabled={
              readOnly || (mode === 'push' && (!pushEndpoint.trim() || !pushAuthKey.trim()))
            }
            onClick={handleLaunchClick}
          >
            启动（{mode}）
          </button>
        </div>
      </div>
    </div>
  )
}
