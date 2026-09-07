/** ModesPanel.tsx — 运行模式卡：trainer 基建编排预设（Pull/Push/Local）+ 推送链路预演 +
 *  rl-config 行为开关（stream / double_buffer / precollect_early，L1 即时应用）。 */

import type { PanelProps } from '../../../ui/view'
import { Toggle } from '../../../ui/components/Toggle'

const PRESETS = [
  {
    mode: 'pull',
    label: 'Pull（Kaggle 拉取）',
    hint: 'self-node+hub-server+隧道+trainer（--ppo remote），Kaggle 入站拉取',
  },
  {
    mode: 'push',
    label: 'Push（推送到 GPU）',
    hint: 'self-node+hub-server+trainer（--ppo remote），GPU 在 Kaggle 侧推送',
  },
  {
    mode: 'local',
    label: 'Local（本机 PPO）',
    hint: '仅 trainer，本机 CPU PPO（remote 互斥组合由 run_rl 启动期 fail-fast 兜底）',
  },
] as const

export function ModesPanel({ stateView, onAction }: PanelProps) {
  if (!stateView) return <div className="tc-loading">加载中…</div>
  const m = stateView.modes
  return (
    <div className="tc-row">
      <div className="tc-col">
        <h4 className="tc-subhead">trainer 基建编排（启动预设）</h4>
        <div className="tc-row" style={{ gap: 8, flexWrap: 'wrap' }}>
          {PRESETS.map((p) => (
            <button
              key={p.mode}
              type="button"
              className={`tc-preset${m.trainerPpo === p.mode ? ' tc-preset--on' : ''}`}
              title={p.hint}
              aria-pressed={m.trainerPpo === p.mode}
              onClick={() => onAction('preset', { mode: p.mode })}
            >
              {p.label}
            </button>
          ))}
        </div>
        <p className="tc-muted tc-small">
          预设=按顺序拉起组件组合；也可在组件表单独启/停。变更即时持久化（console-state）。
        </p>
        <div style={{ marginTop: 10 }}>
          <button
            type="button"
            className="tc-btn"
            title="端到端预演：本机伪 GPU 节点 echo（不跑真 PPO）——发布→推送→回显→落位→作废，账本零污染"
            onClick={() => onAction('smokeTrain', {})}
          >
            推送链路预演（不跑 PPO）
          </button>
        </div>
      </div>
      <div className="tc-col">
        <h4 className="tc-subhead">trainer 行为开关（回写 rl-config.json）</h4>
        <div>
          <Toggle
            label="stream 流式派发"
            note="rl.stream"
            checked={m.stream === 1}
            title="run_rl 的 --stream；本地模式默认开启（AGENTS §15.6），远程模式内部强制 0"
            onChange={(v) => onAction('setMode', { key: 'rl.stream', value: v ? '1' : '0' })}
          />
          <Toggle
            label="双缓冲预采"
            note="rl.double_buffer"
            checked={m.doubleBuffer === 1}
            title="迭代收尾提前采集下一轮（θ_N 快照）"
            onChange={(v) => onAction('setMode', { key: 'rl.double_buffer', value: v ? '1' : '0' })}
          />
          <Toggle
            label="预采提前 spawn"
            note="rl.precollect_early"
            checked={m.precollectEarly === 1}
            title="precollect_early=1 时提前一轮 spawn"
            onChange={(v) =>
              onAction('setMode', { key: 'rl.precollect_early', value: v ? '1' : '0' })
            }
          />
        </div>
        <p className="tc-muted tc-small">
          开关改的是 rl-config.json 的真实键——下次 trainer 启动生效，不影响在跑进程。
        </p>
      </div>
    </div>
  )
}
