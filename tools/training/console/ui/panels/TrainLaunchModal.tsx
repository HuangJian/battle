/** TrainLaunchModal.tsx — 启动 TrainingLoop 的弹窗（工具行并入此处，用户指令）：
 *  选择 trainer 模式（Pull/Push/Local）+ rl-config 行为开关（即时写）+ 推送链路预演入口。
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useState } from 'preact/hooks'
import type { ModeView } from '../../../ui/view'
import { SegmentedControl } from '../../../ui/components/SegmentedControl'
import { Toggle } from '../../../ui/components/Toggle'

export interface TrainLaunchModalProps {
  open: boolean
  modes: ModeView
  onClose: () => void
  onAction: (act: string, body: Record<string, unknown>) => void
  onLaunch: (mode: 'pull' | 'push' | 'local') => void
}

export function TrainLaunchModal({
  open,
  modes,
  onClose,
  onAction,
  onLaunch,
}: TrainLaunchModalProps) {
  const [mode, setMode] = useState<'pull' | 'push' | 'local'>(modes.trainerPpo)
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
        <div className="tc-line">
          <span className="tc-muted tc-small" style={{ minWidth: 90 }}>
            行为开关
          </span>
          <Toggle
            label="stream"
            checked={modes.stream === 1}
            title="rl.stream：run_rl 的 --stream；本地默认开，远程内部强制 0"
            onChange={(v) => onAction('setMode', { key: 'rl.stream', value: v ? '1' : '0' })}
          />
          <Toggle
            label="双缓冲"
            checked={modes.doubleBuffer === 1}
            title="rl.double_buffer"
            onChange={(v) => onAction('setMode', { key: 'rl.double_buffer', value: v ? '1' : '0' })}
          />
          <Toggle
            label="预采"
            checked={modes.precollectEarly === 1}
            title="rl.precollect_early"
            onChange={(v) =>
              onAction('setMode', { key: 'rl.precollect_early', value: v ? '1' : '0' })
            }
          />
        </div>
        <div className="tc-line">
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            title="端到端预演：本机伪 GPU 节点 echo（不跑真 PPO）"
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
            onClick={() => onLaunch(mode)}
          >
            启动（{mode}）
          </button>
        </div>
      </div>
    </div>
  )
}
