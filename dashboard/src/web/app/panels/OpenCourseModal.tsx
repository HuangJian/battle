/** OpenCourseModal.tsx — **开课**弹窗（2026-09-20 用户指令：进程与课程解耦后的独立入口）。
 *
 *  为什么课程级选项（训练模式 / rollout 位置）住在这里而不是「启动服务进程」弹窗：
 *  它们全是**课程级**旋钮（落 rl-config `courses.<课>.*`）——进程是共享的一台，回答不了
 *  「这门课怎么跑」。放在启动弹窗里，操作员点一次「启动」就被迫为一门课做决定；放在这里，
 *  才与「开哪门课」这个动作对齐。
 *
 *  Esc / 遮罩关闭由 App 全局处理。 */

import { useState } from 'preact/hooks'
import type { RolloutSrcMode, TrainMode } from '../../../core/types'
import type { ArchivedCourseView, ModeView } from '../../view'
import { SegmentedControl } from '../../components/SegmentedControl'

export interface OpenCourseModalProps {
  open: boolean
  /** 要开的课程（空 = 弹窗不渲染：没课程就没有「开哪门课」这件事）。 */
  course: string
  /** 该课当前生效值（`modes.rolloutSrc === 'run'` ⇒ 这把键已经是离线档）。 */
  modes: ModeView
  /** 已封存课程（`stateView.archived`）：起点权重选择器的来源（**只读 manifest 的
   *  `weights[]`**，G4-①）。缺省 = 旧视图/尚未封存过 ⇒ 只有 BC 默认一档。 */
  archived?: ArchivedCourseView[] | null
  onClose: () => void
  onConfirm: (opts: {
    trainMode: TrainMode
    rolloutSrc?: RolloutSrcMode
    /** 起点 = 封存课的某个关键轮（缺省 = BC 播种）。服务端按 manifest 自解析路径。 */
    seedFrom?: { sourceCourse: string; it: number }
  }) => void
  /** 局域网只读视图：按钮禁用（服务端 403 兜底）。 */
  readOnly?: boolean
}

const TC_OPEN_TRAIN_MODE = 'tc.openCourse.trainMode'
const TC_OPEN_ROLLOUT = 'tc.openCourse.rolloutSrc'

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
  archived,
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
  // 起点权重来源：`'bc'` = 课程文件 bc 播种（缺省）；否则 `<封存课>:<it>`。
  const [seed, setSeed] = useState('bc')
  // 只列**解析得到路径**的关键轮（glob 提示给不出可直接播种的文件 ⇒ 不列，避免假选项）。
  const sources = (archived ?? []).flatMap((a) =>
    a.weights
      .filter((w) => w.path && !w.path.includes('*'))
      .map((w) => ({
        value: `${a.course}:${w.it}`,
        label: `${a.course} · it${w.it}`,
      })),
  )

  if (!open || !course) return null
  const confirm = (): void => {
    writeLocal(TC_OPEN_TRAIN_MODE, trainMode)
    writeLocal(TC_OPEN_ROLLOUT, rolloutSrc)
    const sep = seed.lastIndexOf(':')
    const seedFrom =
      seed === 'bc' || sep <= 0
        ? undefined
        : { sourceCourse: seed.slice(0, sep), it: Number(seed.slice(sep + 1)) }
    onConfirm({
      trainMode,
      // 离线档忽略 rollout 选择（服务端也会忽略：离线只认 run/run_iters 那对键）。
      rolloutSrc: trainMode === 'online' ? rolloutSrc : undefined,
      ...(seedFrom ? { seedFrom } : {}),
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
        {/* ★ 起点权重（G4-①）：从**封存档案**取关键轮归档权重作新腿起点；只在通知
            「开始」时服务端解析路径并把该文件播成 tmp/<本课>/weights.json（仅在本课
            尚无 weights.json 时生效）。无封存课 ⇒ 只有 BC 默认一档。 */}
        <div className="tc-line">
          <span className="tc-muted tc-small tc-launch__lbl">起点权重</span>
          <select
            className="tc-sel"
            aria-label="起点权重"
            value={seed}
            onChange={(e) => setSeed((e.currentTarget as HTMLSelectElement).value)}
          >
            <option value="bc">BC 默认（课程文件 bc）</option>
            {sources.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        {seed !== 'bc' ? (
          <p className="tc-muted tc-small tc-hint">
            从封存档案取起点：开始会把该归档权重播种成 <code>tmp/{course}/weights.json</code>
            （仅在本课尚无该文件时生效；路径由服务端按 <code>archive-manifest.json</code> 解析）。
          </p>
        ) : null}
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
        {/* ★ §3（2026-09-21）：删掉「降级本机」开关。单一 PPO 路径下没有这个档位——
            PPO 恒为「发布到 hub 队列 + 等 worker 认领」，无人认领就响亮报「等待认领中」。 */}
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
