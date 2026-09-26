/** TrainLaunchModal.tsx — **启动服务进程**的弹窗（工具行并入此处，用户指令）：
 *  隧道/瘦身选项（即时写）+ 行为开关（**已失效，只呈现并标灰**，见 INERT_TOGGLES）
 *  + 推送链路预演入口。
 *
 *  ★ 2026-09-20 用户口径：「服务进程启动不应与课程绑定。进程启动时不要自动开启课程训练」⇒
 *  本弹窗里的选项**只剩进程级**（`rl.*`）：隧道协议/边缘 IP / 瘦身 / 行为开关 / 降级预演。
 *  课程级选项（训练模式、rollout 位置、降级本机）已迁到「开课」弹窗
 *  （`OpenCourseModal.tsx`）——它们落 `courses.<课>.*`，而进程是共享的一台。
 *
 *  ★ 2026-09-19 用户口径：**启动训练不选 pull/push 模式**。
 *   · pull = 远端 worker 自己来领 —— 本机只需保证 hub 在线（配以 tailscale/cloudflared 隧道）；
 *   · push = 系统里**已经登记了** push worker 节点 —— 配置入口是 worker 登记面板，
 *     节点数据住 rl-config.json（`rl.hub_push` + `nodes[].gpu_push`）。
 *  两者都是**部署事实**，不是启动参数；弹窗里因此不再有模式开关与 push 凭据输入。
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useState } from 'preact/hooks'
import type { SlimMode } from '../../../core/types'
import type { ModeView } from '../../view'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Toggle } from '../../components/Toggle'

export interface TrainLaunchModalProps {
  open: boolean
  modes: ModeView
  onClose: () => void
  onAction: (act: string, body: Record<string, unknown>) => void
  /** 启动（**不传任何课程级选项**：那些随「开课」走）。 */
  onLaunch: (opts?: TunnelLaunchOpts) => void
  /** 局域网只读视图：行为开关/预演/启动全部禁用（兜底——启动入口可点，弹窗内禁用以防误操作）。 */
  readOnly?: boolean
}

const TC_CF_PROTOCOL = 'tc.cfProtocol'
const TC_CF_EDGE_IP = 'tc.cfEdgeIp'
const TC_SLIM = 'tc.slim'

/** 「行为开关」这三个键在**单一 PPO 路径下恒不生效**（2026-09-26 用户裁决：别删，标灰 + 写清）。
 *
 *  依据：`nn-training/rl/config.py::validate_args` 把 `stream` / `double_buffer` 恒置 0，而
 *  `precollect_early` 只在 `double_buffer` 开的分支里被读 ⇒ 填什么都不生效。
 *  服务端那侧的回写白名单（`preset.ts::setMode`）**保留**这三个键 —— 将来解冻
 *  intent/多路时直接复用 —— 所以这里只**呈现**：值取 rl-config 当前值（`modes.*`），
 *  **不再**读写 localStorage 偏好（旧行为）：灰着的开关显示成「开」会被读成「它开着且有效」。
 */
const INERT_TOGGLES = [
  { key: 'rl.stream', label: 'stream', checked: (m: ModeView): boolean => m.stream === 1 },
  {
    key: 'rl.double_buffer',
    label: '双缓冲',
    checked: (m: ModeView): boolean => m.doubleBuffer === 1,
  },
  {
    key: 'rl.precollect_early',
    label: '预采',
    checked: (m: ModeView): boolean => m.precollectEarly === 1,
  },
] as const

/** 标灰开关上的字样（用户口径原文）。 */
const INERT_NOTE = '当前不生效'
const INERT_TITLE =
  '单一 PPO 路径下恒置 0（validate_args 强制）：填什么都不生效；保留此开关只为解冻时复用'

export interface TunnelLaunchOpts {
  cfProtocol: 'http2' | 'quic' | 'auto'
  cfEdgeIp: '4' | '6' | 'auto'
  /** M2 协议瘦身回退开关（`'on'|'off'`）：关掉 = 逐字节回到旧字节行为（A/B 对照组）。 */
  slim: SlimMode
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
  const [cfProtocol, setCfProtocol] = useState<'http2' | 'quic' | 'auto'>(() =>
    readTunnelSel(TC_CF_PROTOCOL, modes.cfProtocol, ['http2', 'quic', 'auto'] as const, 'http2'),
  )
  const [cfEdgeIp, setCfEdgeIp] = useState<'4' | '6' | 'auto'>(() =>
    readTunnelSel(TC_CF_EDGE_IP, modes.cfEdgeIp, ['4', '6', 'auto'] as const, '4'),
  )

  const handleLaunchClick = (): void => {
    // 启动即记住本次选项：下次打开弹窗默认继续用它（与服务端 rl-config 双保险）。
    writeLocal(TC_CF_PROTOCOL, cfProtocol)
    writeLocal(TC_CF_EDGE_IP, cfEdgeIp)
    writeLocal(TC_SLIM, slim)
    onLaunch({ cfProtocol, cfEdgeIp, slim })
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
        <h3>启动服务进程</h3>
        {readOnly ? (
          <p className="tc-banner tc-banner--ro tc-banner--flush">
            🔒 只读模式：启动训练仅限本机 localhost 打开控制台操作。
          </p>
        ) : null}
        <p className="tc-muted tc-small tc-mt-0">
          启动**只起进程**：编队恒为 本机 agent → 共享 hub → 共享 trainer。
          <b>启动不会自动开课</b>——「开哪门课」用侧栏课程选择旁的「训练」键。 本轮的 PPO
          去哪，由**部署事实**决定—— 登记了 push worker 节点就走 hub 派发（配置入口在 「push worker
          登记」面板，数据住 rl-config.json）；没登记则等 worker（云机 / 本机） 自己来领，本机只需
          hub 在线（配以 tailscale 直连或 cloudflared 隧道）。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small tc-launch__lbl">隧道</span>
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
        <p className="tc-muted tc-small tc-hint">
          http2 = TCP/443（默认，绕开 ISP 对 QUIC 的 QoS 降质）；auto = 不传旗标（旧行为）。
          改动即时体现在下一次启动的 cloudflared 命令行。当前生效（rl-config）：
          <b>
            {' '}
            {modes.cfProtocol ?? 'http2'}/{modes.cfEdgeIp ?? '4'}
          </b>
          。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small tc-launch__lbl">瘦身</span>
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
        <p className="tc-muted tc-small tc-hint">
          协议瘦身（M2）：开 = opt/ref 走内容寻址 blob，上行 ~4.43MB → ~1.2MB； 关 =
          逐字节回到旧行为（内联 base64 + payload 内冗余文件），拿来做 A/B 对照。
          当前生效（rl-config）：<b>{modes.slim === 'off' ? '关' : '开'}</b>
          ，取值随每轮写入「传输」页的 瘦身 列（事后可分组统计）。
        </p>
        <div className="tc-line tc-toggle-group">
          <span className="tc-muted tc-small">行为开关</span>
          {INERT_TOGGLES.map((t) => (
            <Toggle
              key={t.key}
              label={t.label}
              checked={t.checked(modes)}
              title={`${t.key}：${INERT_TITLE}`}
              note={`${t.key} · ${INERT_NOTE}`}
              disabled
              onChange={() => undefined}
            />
          ))}
        </div>
        <p className="tc-muted tc-small tc-hint">
          上面三个行为开关<b>当前不生效</b>：单一 PPO 路径下 `stream` / `double_buffer` 恒被
          `validate_args` 置 0，`precollect_early` 只在双缓冲开时被读。 开关**保留**（键仍在
          rl-config 的回写白名单里，解冻 intent/多路时可直接复用），标灰即表示此刻不生效。 显示值 =
          rl-config 里的当前值（不再读写浏览器本地偏好 —— 灰着的开关显示成「开」会被
          读成「它开着且有效」）。
        </p>
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
            aria-label="启动服务进程"
            disabled={readOnly}
            onClick={handleLaunchClick}
          >
            启动服务进程
          </button>
        </div>
      </div>
    </div>
  )
}
