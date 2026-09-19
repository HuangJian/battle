/** TrainLaunchModal.tsx — 启动 TrainingLoop 的弹窗（工具行并入此处，用户指令）：
 *  rl-config 行为开关（即时写）+ 隧道/瘦身/rollout 选项 + 推送链路预演入口。
 *
 *  ★ 2026-09-19 用户口径：**启动训练不选 pull/push 模式**。
 *   · pull = 远端 worker 自己来领 —— 本机只需保证 hub 在线（配以 tailscale/cloudflared 隧道）；
 *   · push = 系统里**已经登记了** push worker 节点 —— 配置入口是 worker 登记面板，
 *     节点数据住 rl-config.json（`rl.hub_push` + `nodes[].gpu_push`）。
 *  两者都是**部署事实**，不是启动参数；弹窗里因此不再有模式开关与 push 凭据输入。
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useEffect, useRef, useState } from 'preact/hooks'
import type { RolloutSrcMode, SlimMode } from '../../../core/types'
import type { ModeView } from '../../view'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Toggle } from '../../components/Toggle'
import { TC_TRAIN_TOGGLES } from '../../view'

export interface TrainLaunchModalProps {
  open: boolean
  modes: ModeView
  onClose: () => void
  onAction: (act: string, body: Record<string, unknown>) => void
  onLaunch: (opts?: TunnelLaunchOpts & { remoteDegrade?: boolean }) => void
  /** 局域网只读视图：行为开关/预演/启动全部禁用（兜底——启动入口可点，弹窗内禁用以防误操作）。 */
  readOnly?: boolean
}

const TC_REMOTE_DEGRADE = 'tc.remoteDegrade'
const TC_CF_PROTOCOL = 'tc.cfProtocol'
const TC_CF_EDGE_IP = 'tc.cfEdgeIp'
const TC_SLIM = 'tc.slim'
const TC_ROLLOUT_SRC = 'tc.rolloutSrc'

export interface TunnelLaunchOpts {
  cfProtocol: 'http2' | 'quic' | 'auto'
  cfEdgeIp: '4' | '6' | 'auto'
  /** M2 协议瘦身回退开关（`'on'|'off'`）：关掉 = 逐字节回到旧字节行为（A/B 对照组）。 */
  slim: SlimMode
  /** M3 rollout 执行位置（`'local'|'node'|'auto'`）：node = 本轮整轮上云（A/B 对照）。 */
  rolloutSrc: RolloutSrcMode
}

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

/** 隧道选项初值：localStorage（上次选择）→ 服务端当前生效值 → 缺省（M1）。 */
function readTunnelSel<T extends string>(
  key: string,
  fromServer: string | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  const local = readLocal(key)
  if ((allowed as readonly string[]).includes(local)) return local as T
  if (fromServer && (allowed as readonly string[]).includes(fromServer)) return fromServer as T
  return fallback
}

export function TrainLaunchModal({
  open,
  modes,
  onClose,
  onAction,
  onLaunch,
  readOnly,
}: TrainLaunchModalProps) {
  // M1：隧道协议/边缘 IP。选中值优先 localStorage（上次选择），否则服务端当前生效值。
  const [slim, setSlim] = useState<SlimMode>(() =>
    readTunnelSel(TC_SLIM, modes.slim, ['on', 'off'] as const, 'on'),
  )
  // M3：rollout 执行位置。缺省 local（= 历史行为），与服务端解析口径一致。
  const [rolloutSrc, setRolloutSrc] = useState<RolloutSrcMode>(() =>
    readTunnelSel(TC_ROLLOUT_SRC, modes.rolloutSrc, ['local', 'node', 'auto'] as const, 'local'),
  )
  const [cfProtocol, setCfProtocol] = useState<'http2' | 'quic' | 'auto'>(() =>
    readTunnelSel(TC_CF_PROTOCOL, modes.cfProtocol, ['http2', 'quic', 'auto'] as const, 'http2'),
  )
  const [cfEdgeIp, setCfEdgeIp] = useState<'4' | '6' | 'auto'>(() =>
    readTunnelSel(TC_CF_EDGE_IP, modes.cfEdgeIp, ['4', '6', 'auto'] as const, '4'),
  )
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

  const applyToggle = (key: string, v: boolean): void => {
    const next = { ...toggles, [key]: v }
    setToggles(next)
    onAction('setMode', { key, value: v ? '1' : '0' })
  }

  const handleLaunchClick = (): void => {
    // 启动即记住本次选项：下次打开弹窗默认继续用它（与服务端 rl-config 双保险）。
    writeLocal(TC_REMOTE_DEGRADE, remoteDegrade ? '1' : '0')
    writeLocal(TC_CF_PROTOCOL, cfProtocol)
    writeLocal(TC_CF_EDGE_IP, cfEdgeIp)
    writeLocal(TC_SLIM, slim)
    writeLocal(TC_ROLLOUT_SRC, rolloutSrc)
    onLaunch({ remoteDegrade, cfProtocol, cfEdgeIp, slim, rolloutSrc })
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
        <p className="tc-muted tc-small" style={{ marginTop: 0 }}>
          启动**不选模式**：编队恒为 本机 agent → 共享 hub → 共享 trainer。本轮的 PPO
          去哪，由**部署事实**决定—— 登记了 push worker 节点就走 hub 派发（配置入口在「push worker
          登记」面板，数据住 rl-config.json）；没登记则等 worker（云机 / 本机）自己来领，本机只需
          hub 在线（配以 tailscale 直连或 cloudflared 隧道）。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small" style={{ minWidth: 90 }}>
            隧道
          </span>
          <SegmentedControl<'http2' | 'quic' | 'auto'>
            value={cfProtocol}
            ariaLabel="隧道协议"
            options={[
              { value: 'http2', label: 'http2' },
              { value: 'quic', label: 'quic' },
              { value: 'auto', label: 'auto' },
            ]}
            onChange={setCfProtocol}
          />
          <span className="tc-muted tc-small">边缘 IP</span>
          <SegmentedControl<'4' | '6' | 'auto'>
            value={cfEdgeIp}
            ariaLabel="边缘 IP 版本"
            options={[
              { value: '4', label: '4' },
              { value: '6', label: '6' },
              { value: 'auto', label: 'auto' },
            ]}
            onChange={setCfEdgeIp}
          />
        </div>
        <p className="tc-muted tc-small" style={{ marginTop: -4 }}>
          http2 = TCP/443（默认，绕开 ISP 对 QUIC 的 QoS 降质）；auto = 不传旗标（旧行为）。
          改动即时体现在下一次启动的 cloudflared 命令行。当前生效（rl-config）：
          <b>
            {' '}
            {modes.cfProtocol ?? 'http2'}/{modes.cfEdgeIp ?? '4'}
          </b>
          。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small" style={{ minWidth: 90 }}>
            瘦身
          </span>
          <SegmentedControl<SlimMode>
            value={slim}
            ariaLabel="协议瘦身"
            options={[
              { value: 'on', label: '开' },
              { value: 'off', label: '关（A/B 对照）' },
            ]}
            onChange={setSlim}
          />
        </div>
        <p className="tc-muted tc-small" style={{ marginTop: -4 }}>
          协议瘦身（M2）：开 = opt/ref 走内容寻址 blob，上行 ~4.43MB → ~1.2MB； 关 =
          逐字节回到旧行为（内联 base64 + payload 内冗余文件），拿来做 A/B 对照。
          当前生效（rl-config）：<b>{modes.slim === 'off' ? '关' : '开'}</b>
          ，取值随每轮写入「传输」页的 瘦身 列（事后可分组统计）。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small" style={{ minWidth: 90 }}>
            rollout
          </span>
          <SegmentedControl<RolloutSrcMode>
            value={rolloutSrc}
            ariaLabel="rollout 执行位置"
            options={[
              { value: 'local', label: '本机' },
              { value: 'node', label: '上云（节点）' },
              { value: 'auto', label: 'auto' },
            ]}
            onChange={setRolloutSrc}
          />
        </div>
        <p className="tc-muted tc-small" style={{ marginTop: -4 }}>
          rollout 位置（M3）：本机 = 本机采样 + 只把 PPO 送云（历史行为）； 上云（节点）=
          本轮**整轮**上云（节点跑 exporter 产 shard 再跑 PPO，撤掉上行 1.2MB payload， 适合 TPU
          实例）；auto = 不表态，交回课程配置解析。 当前生效（rl-config）：
          <b>
            {' '}
            {modes.rolloutSrc === 'node'
              ? '上云（节点）'
              : modes.rolloutSrc === 'auto'
                ? 'auto'
                : '本机'}
          </b>
          ，取值随每轮写入「传输」页的 rollout 列（事后可分组统计）。
        </p>
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
            aria-label="启动 TrainingLoop"
            disabled={readOnly}
            onClick={handleLaunchClick}
          >
            启动训练栈
          </button>
        </div>
      </div>
    </div>
  )
}
