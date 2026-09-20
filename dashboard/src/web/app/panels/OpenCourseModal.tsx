/** OpenCourseModal.tsx — **开课**弹窗（2026-09-20 用户指令：进程与课程解耦后的独立入口）。
 *
 *  为什么课程级选项（训练模式 / rollout 位置 / 降级本机）住在这里而不是「启动服务进程」弹窗：
 *  它们全是**课程级**旋钮（落 rl-config `courses.<课>.*`）——进程是共享的一台，回答不了
 *  「这门课怎么跑」。放在启动弹窗里，操作员点一次「启动」就被迫为一门课做决定；放在这里，
 *  才与「开哪门课」这个动作对齐。
 *
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useState } from 'preact/hooks'
import type { RolloutSrcMode, TrainMode } from '../../../core/types'
import type { ModeView } from '../../view'
import { SegmentedControl } from '../../components/SegmentedControl'
import { Toggle } from '../../components/Toggle'

export interface OpenCourseModalProps {
  open: boolean
  /** 要开的课程（空 = 弹窗不渲染：没课程就没有「开哪门课」这件事）。 */
  course: string
  /** 该课当前生效值（`modes.rolloutSrc === 'run'` ⇒ 这把键已经是离线档）。 */
  modes: ModeView
  onClose: () => void
  onConfirm: (opts: {
    trainMode: TrainMode
    rolloutSrc?: RolloutSrcMode
    remoteDegrade: boolean
  }) => void
  /** 局域网只读视图：按钮禁用（服务端 403 兜底）。 */
  readOnly?: boolean
}

const TC_OPEN_TRAIN_MODE = 'tc.openCourse.trainMode'
const TC_OPEN_ROLLOUT = 'tc.openCourse.rolloutSrc'
const TC_OPEN_DEGRADE = 'tc.openCourse.degrade'

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

export function OpenCourseModal({
  open,
  course,
  modes,
  onClose,
  onConfirm,
  readOnly,
}: OpenCourseModalProps) {
  // 训练模式：**以 rl-config 为准**（`run` ⇒ 这门课正处离线档），localStorage 只记上次点选。
  const [trainMode, setTrainMode] = useState<TrainMode>(() =>
    modes.rolloutSrc === 'run'
      ? 'offline'
      : readLocal(TC_OPEN_TRAIN_MODE) === 'offline'
        ? 'offline'
        : 'online',
  )
  const [rolloutSrc, setRolloutSrc] = useState<RolloutSrcMode>(() => {
    const local = readLocal(TC_OPEN_ROLLOUT)
    if (local === 'local' || local === 'node' || local === 'auto') return local
    return modes.rolloutSrc === 'node' || modes.rolloutSrc === 'auto' ? modes.rolloutSrc : 'local'
  })
  const [remoteDegrade, setRemoteDegrade] = useState(() => readLocal(TC_OPEN_DEGRADE) === '1')

  if (!open || !course) return null
  const confirm = (): void => {
    writeLocal(TC_OPEN_TRAIN_MODE, trainMode)
    writeLocal(TC_OPEN_ROLLOUT, rolloutSrc)
    writeLocal(TC_OPEN_DEGRADE, remoteDegrade ? '1' : '0')
    onConfirm({
      trainMode,
      // 离线档忽略 rollout 选择（服务端也会忽略：离线只认 run/run_iters 那对键）。
      rolloutSrc: trainMode === 'online' ? rolloutSrc : undefined,
      remoteDegrade,
    })
  }
  return (
    <div className="tc-modal-mask" onClick={onClose}>
      <div
        className="tc-modal"
        role="dialog"
        aria-label={`开课 ${course}`}
        onClick={(e) => e.stopPropagation()}
      >
        <h3>开课 {course}</h3>
        {readOnly ? (
          <p className="tc-banner tc-banner--ro tc-banner--flush">
            🔒 只读模式：开课/停课仅限本机 localhost 打开控制台操作。
          </p>
        ) : null}
        <p className="tc-muted tc-small tc-mt-0">
          开课 = 把这门课**放进训练**：写课程级旋钮（<code>rl-config.json</code> 的{' '}
          <code>courses.{course}.*</code>）+ 建发现事实（
          <code>tmp/{course}/training_log.jsonl</code> 与 <code>remote-jobs/</code>）+ 解除暂停意图
          + 按下面选定的模式置 hub 派发闸。
          <b>共享 trainer 没在跑也能开</b>——它是发现式的，进程一起就会扫到这门课；
          停课则相反（写暂停意图 + hub 置离线，队列与账本一个字不动）。
        </p>
        <div className="tc-line">
          <span className="tc-muted tc-small tc-launch__lbl">训练模式</span>
          <SegmentedControl<TrainMode>
            value={trainMode}
            ariaLabel="训练模式"
            options={[
              { value: 'online', label: '在线' },
              { value: 'offline', label: '离线' },
            ]}
            onChange={setTrainMode}
          />
        </div>
        {trainMode === 'offline' ? (
          <p className="tc-muted tc-small tc-hint">
            离线（缺省在线）：本机**不跑** rollout/PPO。开课后本课 <code>rollout_src=run</code> +{' '}
            <code>run_iters=-1</code>（整段），hub 该课置 offline——整段 job 只交给**带标**
            worker（云机跑 <code>battle.offline.ipynb</code>）； 也可停课后在「导出」里拿{' '}
            <code>task-&lt;课&gt;.zip</code> 人工搬上云。
          </p>
        ) : (
          <>
            <div className="tc-line">
              <span className="tc-muted tc-small tc-launch__lbl">rollout</span>
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
            <p className="tc-muted tc-small tc-hint">
              rollout 位置：本机 = 本机采样 + 只把 PPO 送云； 上云（节点）= 本轮**整轮**上云； auto
              = 不表态，交回 <code>rl.rollout_src</code> 解析。写的是**本课**的覆盖 （
              <code>courses.{course}.rollout_src</code>）——不碰其它课程共用的默认面。
            </p>
          </>
        )}
        <div className="tc-line tc-toggle-group">
          <Toggle
            label="降级本机"
            checked={remoteDegrade}
            title={
              '远端 PPO 连败是否降级到本机进程内 PPO（本课旋钮 courses.<课>.remote_degrade_after）。\n' +
              '· 关（默认）：连败 3 次写 ABORT 停腿——不静默切慢速本机。\n' +
              '· 开：连败 3 次后懒加载本机 torch/model 并继续训练。\n' +
              '开课时施加；已开着的课要**停课 → 重新开课**（或重启共享 trainer）才换。'
            }
            disabled={readOnly}
            onChange={setRemoteDegrade}
          />
        </div>
        <div className="tc-modal__foot">
          <span className="sp" />
          <button type="button" className="tc-btn" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="tc-btn tc-btn--primary"
            aria-label={`开课 ${course}`}
            disabled={readOnly}
            onClick={confirm}
          >
            开课
          </button>
        </div>
      </div>
    </div>
  )
}
