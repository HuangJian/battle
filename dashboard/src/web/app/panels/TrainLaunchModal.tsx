/** TrainLaunchModal.tsx — 启动 TrainingLoop 的弹窗（工具行并入此处，用户指令）：
 *  选择 trainer 模式（Pull/Push/Local）+ rl-config 行为开关（即时写）+ 推送链路预演入口。
 *  Push 模式：endpoint + auth key 可选（填了才 ping 门 + 回写 rl-config）；留空依次尝试
 *  复用 config 的 gpu_push、回落本机 worker_server（2026-09-15 一键本机 push）。
 *  Local 模式（2026-09-15 起）：本机 PPO 独立成进程——预设会先起 hub-server 与
 *  local-worker，trainer 走 --ppo remote + 本机 hub（进程内 PPO 已不是控制台选项）。
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
  onLaunch: (
    mode: 'pull' | 'push' | 'local',
    opts?: Partial<PushCredentials> & { remoteDegrade?: boolean },
  ) => void
  /** 局域网只读视图：行为开关/预演/启动全部禁用（兜底——启动入口可点，弹窗内禁用以防误操作）。 */
  readOnly?: boolean
}

const TC_PUSH_ENDPOINT = 'tc.pushEndpoint'
const TC_PUSH_AUTH = 'tc.pushAuthKey'
const TC_REMOTE_DEGRADE = 'tc.remoteDegrade'

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
  // T7：远端连败是否 opt-in 降级本机进程内 PPO。默认关（连败 3 次 ABORT 停腿）。
  // 历史默认 3 会静默切到慢速本机，且曾撞上 None backend。
  const [remoteDegrade, setRemoteDegrade] = useState(() => {
    try {
      return localStorage.getItem(TC_REMOTE_DEGRADE) === '1'
    } catch {
      return false
    }
  })

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
    writeLocal(TC_REMOTE_DEGRADE, remoteDegrade ? '1' : '0')
    if (mode !== 'push') {
      onLaunch(mode, { remoteDegrade })
      return
    }
    const endpoint = pushEndpoint.trim()
    const authKey = pushAuthKey.trim()
    // 留空合法：服务端复用 rl-config 中 enabled 且 ping 通的 gpu_push。
    // 填了 endpoint 则必须同时给 auth key。
    if (endpoint && !authKey) {
      setPushErr('填写 endpoint 时必须同时填写 auth key')
      return
    }
    setPushErr('')
    if (endpoint) {
      writeLocal(TC_PUSH_ENDPOINT, endpoint)
      writeLocal(TC_PUSH_AUTH, authKey)
    }
    onLaunch(mode, { endpoint, authKey, remoteDegrade })
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
        {mode === 'local' ? (
          <p className="tc-muted tc-small" style={{ marginTop: 4 }}>
            Local = 本机独立 PPO worker（与云端 worker 同一份代码）：预设依次拉起 hub-server →
            local-worker → trainer（--ppo remote，pull 本机 hub）。worker
            是独立进程，训练途中可单独启停/换代码重启。
          </p>
        ) : null}
        {mode === 'push' ? (
          <div className="tc-push-creds" style={{ display: 'grid', gap: 8, marginTop: 4 }}>
            <label className="tc-line" style={{ display: 'grid', gap: 4 }}>
              <span className="tc-muted tc-small">
                endpoint（留空 = 复用 config 可用 gpu_push，否则回落本机 worker_server）
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
                云机自起 cloudflared；本机不启 hub-server。留空先检 config 中 enabled gpu_push 的
                /ping，通了直接启动；都没有则回落本机 worker_server（自动拉起 workerServer
                组件并把本课 push 目标指到本机，不动你配置里的云节点）。
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
          <Toggle
            label="降级本机"
            checked={remoteDegrade}
            title={
              '远端 PPO 连败是否降级到本机进程内 PPO（--remote-degrade-after）。\n' +
              '· 关（默认）：连败 3 次写 ABORT 停腿——不静默切慢速本机。\n' +
              '· 开：连败 3 次后懒加载本机 torch/model 并继续训练。\n' +
              '仅启动时生效；监督重启会复现当前开关。'
            }
            disabled={readOnly}
            onChange={setRemoteDegrade}
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
            disabled={readOnly || (mode === 'push' && !!pushEndpoint.trim() && !pushAuthKey.trim())}
            onClick={handleLaunchClick}
          >
            启动（{mode}）
          </button>
        </div>
      </div>
    </div>
  )
}
