/** CourseMatrix.tsx — 课程矩阵：把「并行课程总览」（hub 侧）与「训练调度器」（训练侧）
 *  合并成**一张**表（问题 C5）。合并规则与全部纯函数在 `view/course-matrix.ts`。
 *
 *  ## 为什么合并（而不是并排摆两块）
 *
 *  两块卡回答的是同一个问题——「这门课现在怎么样」——却各写一半，而且**互相看不见**。
 *  于是最该看到的两种局面在两边各自都是「正常」的：
 *
 *  - hub 在给一门**没有任何进程推进**的课派活 ⇒ job 越堆越多，没人消费；
 *  - 一门课**疯狂训练但 hub 没注册它** ⇒ PPO 永远不被派发，算力白烧（rollout 白跑）。
 *
 *  合并之后这两条落在**状态列**上（`hub 已注册 · 无进程` / `在训 · hub 未注册`），也就是
 *  操作员第一眼看的那一列。
 *
 *  ## 为什么这里是一张 `<table>` 而不是 `StatusRow` 芯片行
 *
 *  `StatusRow` 是「一行实体 + 状态 + 指标 + 动作」的**芯片行**原语（组件卡 / 节点 / worker）：
 *  它是 `inline-flex` 且会换行，**跨行不对齐**。矩阵的七列是要**竖着比**的（哪门课队列最深、
 *  哪门课的接管多久没回传）——芯片行拼出来的「表」在同一列上每行宽度都不同，比较只能靠读。
 *  所以矩阵用真表格（`tc-table` 基础样式 + `tabular-nums` 数字列），但**状态词表与语义档
 *  仍走 `matrixStatus` + `StatusDot`**（一个状态只有一个说法、一个颜色），这条才是 §4.2 的实质。
 *
 *  ## 只列在训课程（2026-09-20 用户指令）
 *
 *  `mergeCourseRows` 给的是**全集**（outer join 不丢课）；这里用 `isTrainingRow` 再筛一道，
 *  未在训的行不上屏——理由：这张表是「盯着正在跑的那几门」，而未在训的课在盘上会有几十门
 *  （历史课都有账本与目录），它们把在训的行挤出首屏。**代价**：`hub 已注册 · 无进程` 这种
 *  「两半事实打架」的行不再直接上屏 ⇒ 表头留一个计数 chip（悬停逐门点名 + 各自状态），
 *  未列的课不静默消失（DECISIONS §2026-09-20-console-declutter · 全文 → docs/nn/console.md §11）。
 *
 *  ## 只读语义
 *
 *  LAN 只读时动作按钮**照常渲染且可点**（§7 O1）：只读是服务端边界（403），不是把按钮涂灰。
 *  `onAction` 缺省 = 这个渲染上下文没有动作通道（单测/无动作能力），此时**不假装能控**、
 *  一个都不渲染——与合并前两张卡同一判据。
 */

import { useState } from 'preact/hooks'
import {
  FROZEN_RECLAIMS,
  type ArchivedCourseView,
  frozenJobs,
  isTrainingRow,
  kindBadge,
  type CourseMatrixRow,
  type LoopComplete,
  type LoopQueueView,
  type ParallelOverviewView,
  matrixBadgeTone,
  matrixDotTone,
  matrixFoot,
  matrixMeta,
  mergeCourseRows,
  pauseOp,
  pendingTitle,
  stepTitle,
  waitingClass,
} from '../../view'
import { Collapsible } from '../../components/Collapsible'
import { Empty } from '../../components/Empty'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusDot } from '../../components/StatusDot'
import { BundleRowActions } from './BundleRowActions'

/** 封存分组：一个计数 chip + **默认折叠**的档案清单（plan/course-archive.plan.md §4 S3）。
 *
 *  数据源是 `stateView.archived`（**只读 manifest，不扫盘、不解压**）——这里只负责呈现：
 *  每行给「哪门 / 什么形态 / it 区间 / 还有没有可复算的 shards」。最后一栏是**边界声明**，
 *  不是细节：`shards 未留存 ⇒ 不可复算，只可比`，不说清楚就会有人拿它去重跑 rollout。
 */
export function ArchiveGroup({
  arch,
  open,
  onToggle,
}: {
  arch: ArchivedCourseView[]
  open: boolean
  onToggle: (next: boolean) => void
}) {
  if (arch.length === 0) return null
  return (
    <div className="tc-mx__arch">
      <button
        type="button"
        className="tc-mx__chip tc-mx__arch-toggle"
        aria-expanded={open}
        title={
          `已封存的 ${arch.length} 门课程（只读档案在 archive/courses/<课>/）。` +
          '封存课不进训练、不参与派发，也已从课程选择器/开课弹窗里排除；' +
          '需要起点时用档案里的 weights[]（指向 nn-training/weights/）。'
        }
        onClick={() => onToggle(!open)}
      >
        {`封存 ${arch.length} 门 ${open ? '▾' : '▸'}`}
      </button>
      <Collapsible collapsed={!open}>
        <ul className="tc-mx__arch-list">
          {arch.map((a) => (
            <li key={a.course} className="tc-mx__arch-row">
              <span className="tc-mx__arch-name">{a.course}</span>
              <span
                className="tc-mx__arch-meta"
                title={
                  `形态 ${a.form}（A=旧 it<N>/dist/；B=新 it<N>/w<id>/；C=offline 回传）· ` +
                  `it ${a.itRange[0]}–${a.itRange[1]}（终点 ${a.finalIt}）· ` +
                  `codec ${a.codec} · 档案 ${(a.bytesTotal / 1_000_000).toFixed(1)} MB` +
                  (a.parent ? ` · 父臂 ${a.parent}` : '')
                }
              >
                {`形态 ${a.form} · it ${a.itRange[0]}–${a.itRange[1]} · `}
                <span className={a.shardsKept ? 'tc-mx__arch-ok' : ''}>
                  {a.shardsKept ? '含 shards（可复算）' : '无可复算 shards（只可比）'}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </Collapsible>
    </div>
  )
}

/** 切课按钮的悬停（导出给用例断言，避免文案与断言两处漂移）。 */
export function pickTitle(course: string, viewing: boolean): string {
  return viewing ? `${course}（当前查看）` : `切到查看 ${course}`
}

export interface CourseMatrixProps {
  /** hub 侧事实（`null` = 没读到）。 */
  overview: ParallelOverviewView | null
  /** 训练侧事实（`null` = 只读视图不可用）。 */
  loopQueue: LoopQueueView | null
  // ★M4：`modeIntents`（控制台意图）与 `courseRolloutSrc`（逐课 rl-config 快照）两个入参
  //  随模式语义一起退役——它们存在的前提是「意图 / hub 模式 / rl-config 三个源可能没对齐」，
  //  而今天「这门课归谁」只有一个真源：hub 的 hold（见 `course-matrix.ts` 尾注）。
  //  渲染器不再有「漂移徽标」这一栏，也不再有「意图」可摆。
  /** ★2026-10-06：逐课停车态（`stateView.loopCompletes`，账本尾行 `run_complete`）——
   *  状态列与 pill 同源说「已收官」（缺省 = 旧视图，行为不变）。 */
  loopCompletes?: Record<string, LoopComplete> | null
  /** 当前查看课程（高亮）。 */
  course: string
  onSelectCourse: (course: string) => void
  /** 动作通道。缺省 = 不渲染动作列（见文件头注）。 */
  onAction?: (act: string, body: Record<string, unknown>) => unknown
  /** 已封存课程（`stateView.archived`；缺省 = 旧视图/尚未封存过 ⇒ 不画封存分组）。
   *
   *  为什么进这张表：封存课**已从 `discoverCourses()` 排除**（否则 curricula 回填会把它
   *  捞回课程 select），于是它在这里**根本没有行**——但「它还在、只是封存了」这件事得有个
   *  去处，否则操作员会以为档案丢了。默认折叠：它不属于「盯着在跑的那几门」（见文件头注
   *  「只列在训课程」），一行的信息量只有「哪门、什么形态、还能不能复算」。 */
  archived?: ArchivedCourseView[] | null
}

export function CourseMatrix({
  overview,
  loopQueue,
  loopCompletes,
  course,
  onSelectCourse,
  onAction,
  archived,
}: CourseMatrixProps) {
  // 「接管多久没进度 / 产物多久没动」要当下时刻：读表在这里发生，纯函数只收数字（可单测、可回放）。
  const nowSec = Math.floor(Date.now() / 1000)
  // 封存分组**默认折叠**（不属于「盯着在跑的那几门」）。
  const [archOpen, setArchOpen] = useState(false)
  const arch = archived ?? []
  const all = mergeCourseRows({
    overview,
    queue: loopQueue,
    loopCompletes,
    viewing: course,
    nowSec,
  })
  // 只列在训课程（见文件头注）：未在训的行留计数 chip，不静默消失。
  const rows = all.filter(isTrainingRow)
  const hidden = all.filter((r) => !isTrainingRow(r))

  // 两侧都没东西可说时不留空壳；但**读失败必须显因**（不静默）——那是运维唯一能修的线索。
  if (all.length === 0) {
    const err = loopQueue?.error
    if (!err) {
      // ★ 全部课程都封存了（`all` 空、封存非空）也是合法稳态：不画封存分组就等于
      //   把「档案还在」这件事一起藏了。
      if (arch.length === 0) return null
      return (
        <section className="tc-mx" aria-label="课程矩阵">
          <SectionHeader title="课程" />
          <ArchiveGroup arch={arch} open={archOpen} onToggle={setArchOpen} />
        </section>
      )
    }
    return (
      <section className="tc-mx" aria-label="课程矩阵">
        <SectionHeader title="课程" />
        <Empty kind="error" reason={`只读视图不可用：${err}`} />
        <ArchiveGroup arch={arch} open={archOpen} onToggle={setArchOpen} />
      </section>
    )
  }

  const meta = matrixMeta({ overview, queue: loopQueue })
  // §4.1：被毒包熔断冻住的 job（跨课程展平）。它们**不在 pending 里**——只给队列深度的话，
  // 操作员看到的只是「队列短了」，看不到「这份 payload 已认领 N 次零回传、hub 把它拿出了池子」。
  const frozen = frozenJobs(overview)
  return (
    <section className="tc-mx" aria-label="课程矩阵">
      <SectionHeader
        title="课程"
        count={rows.length}
        hint={
          '只列在训课程（共享 trainer 在跑 ∧ 该课未收官）：hub 侧（派活/队列/接管）与训练侧' +
          '（指针/卡在哪一步/在等什么）合并成一行。未在训的课不在此表——表头 chip 给出未列门数'
        }
      />
      {frozen.length > 0 ? (
        <div className="tc-mx__frozen" role="group" aria-label="毒包熔断冻结">
          {/* 一屏一条：事故现场（同一份 payload 反复炸）+ 唯一的可逆口（逐 job 人工解冻）。
              解冻按钮**照常渲染**（只读是服务端边界 403，不是涂灰），无动作通道时才不渲染。 */}
          {frozen.map((f) => (
            <div key={`${f.course}/${f.jobId}`} className="tc-mx__frozenrow">
              <span className="tc-mx__frozentitle">{`毒包熔断 · ${f.course} · ${f.jobId}`}</span>
              <span
                className="tc-mx__frozenmeta"
                title={
                  `认领后**零回传** ${f.reclaims} 次（hub 阈值 ${FROZEN_RECLAIMS}）⇒ 该 payload 已移出可领取池。` +
                  '冻结 ≠ 死刑：确认它坏了就重发新字节（新 job_id，重发不解除冻结）；' +
                  `确认无碍才解冻回池。最后认领者：${f.worker || '（无身份）'}`
                }
              >
                {`零回传 ${f.reclaims} 次 · ${
                  f.worker ? `最后认领 ${f.worker}` : '最后认领（无身份）'
                }`}
              </span>
              {onAction ? (
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  aria-label={`解冻 ${f.course} 的 ${f.jobId}`}
                  title={`人工解冻 ${f.jobId}：清冻结 + 清零回传计数，job 立即回池可重领`}
                  onClick={() =>
                    void onAction('unfreeze-job', { course: f.course, jobId: f.jobId })
                  }
                >
                  解冻
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      <div className="tc-mx__meta">
        {/* 「单例」是训练进程的形态事实（不是读数）：一个进程服务所有并行课程，每课一条队列。 */}
        <span className="tc-mx__singleton" title="一个进程服务所有并行课程（每课一条任务队列）">
          单例
        </span>
        {meta.map((m) => (
          <span
            key={m.text}
            className={`tc-mx__chip${m.tone ? ` tc-mx__chip--${m.tone}` : ''}`}
            title={m.title}
          >
            {m.text}
          </span>
        ))}
        {hidden.length > 0 ? (
          // 未列的课：一句计数 + 悬停逐门点名（各自的状态词就是表里那个口径）——
          // 「没上屏」不等于「不存在」（历史课会积几十门）。
          <span
            className="tc-mx__chip"
            title={
              `本表只列在训课程。未列的 ${hidden.length} 门：\n` +
              hidden.map((r) => `· ${r.course} —— ${r.status.text}`).join('\n')
            }
          >
            {`未在训 ${hidden.length} 门未列`}
          </span>
        ) : null}
      </div>
      <ArchiveGroup arch={arch} open={archOpen} onToggle={setArchOpen} />
      {rows.length === 0 ? (
        // ★ 0 门在训是**合法稳态**（都收官了 / 进程没跑）：显因，不整块消失。
        <Empty
          kind={loopQueue?.error ? 'error' : 'empty'}
          reason={
            loopQueue?.error
              ? `只读视图不可用：${loopQueue.error}`
              : !loopQueue
                ? '训练侧只读视图不可用——哪几门课在训**不可知**（不是「没有课在训」）'
                : '当前没有在训课程：本表只列在训的课（开课标记或进程任一在训就上屏；已收官/未开课的见上方 chip 悬停）'
          }
        >
          {loopQueue && !loopQueue.error ? (
            <>
              开课走侧栏课程选择器旁的「训练」；在训课程另见顶栏 pill 行
              {hidden.length > 0 ? `（未列的 ${hidden.length} 门见上方 chip 悬停）` : null}。
            </>
          ) : null}
        </Empty>
      ) : (
        <div className="tc-tablewrap">
          <table className="tc-table tc-mx__table">
            <thead>
              <tr>
                <th scope="col">课程</th>
                <th scope="col">状态</th>
                <th scope="col" className="tc-mx__num">
                  iter
                </th>
                <th scope="col">本轮</th>
                <th scope="col">在等什么</th>
                <th scope="col" className="tc-mx__num">
                  队列 · 在飞
                </th>
                <th scope="col">接管</th>
                {onAction ? <th scope="col">操作</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <MatrixTr
                  key={r.course}
                  row={r}
                  onSelectCourse={onSelectCourse}
                  onAction={onAction}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {/* 页脚口径拿的是**全集**（它讲的是「谁在等外部 / 读面可不可用」，与上屏筛无关）。 */}
      <div className="tc-mx__foot">{matrixFoot({ queue: loopQueue, rows: all })}</div>
    </section>
  )
}

function MatrixTr({
  row: r,
  onSelectCourse,
  onAction,
}: {
  row: CourseMatrixRow
  onSelectCourse: (course: string) => void
  onAction?: (act: string, body: Record<string, unknown>) => unknown
}) {
  const lq = r.lq
  const kind = lq ? kindBadge(lq) : null
  const pause = pauseOp(lq)
  const badgeTone = matrixBadgeTone(r.status.tone)
  return (
    <tr
      className={`tc-mx__row${r.viewing ? ' tc-mx__row--cur' : ''}${
        lq && !lq.training ? ' tc-mx__row--stopped' : ''
      }`}
      // 冲突行整行加底色：它不是某一格的问题，是两半事实在打架。
      data-conflict={r.conflict ?? undefined}
    >
      <th scope="row" className="tc-mx__course">
        {/* 切课按钮只包住课程名（不是整行）：整行可点会让表格里的文字选不中，
            而这一列旁边就有「操作」列的两个开关。 */}
        <button
          type="button"
          className="tc-mx__pick"
          aria-current={r.viewing ? 'true' : undefined}
          title={pickTitle(r.course, r.viewing)}
          onClick={() => onSelectCourse(r.course)}
        >
          <StatusDot tone={matrixDotTone(r.status.tone)} title={r.status.title} />
          <b>{r.course}</b>
        </button>
        {kind ? (
          <span className={`tc-badge tc-badge--${kind.tone}`} title={kind.title}>
            {kind.text}
          </span>
        ) : null}
        {/* ★M4：接管徽标（接管掉线 / 导包中）。live 的接管不画——状态列与「接管」列已经在说它
            （同一件事不摆三遍），`null` = 没这两件事。 */}
        {r.holdBadge ? (
          <span className={`tc-badge tc-badge--${r.holdBadge.tone}`} title={r.holdBadge.title}>
            {r.holdBadge.text}
          </span>
        ) : null}
        {/* ★P1-6：租约徽标（可接管 = 静默超阈；已撤租 = 墓碑）——只给需处置的两档。 */}
        {r.leaseBadge ? (
          <span className={`tc-badge tc-badge--${r.leaseBadge.tone}`} title={r.leaseBadge.title}>
            {r.leaseBadge.text}
          </span>
        ) : null}
      </th>
      <td>
        <span className={`tc-badge tc-badge--${badgeTone}`} title={r.status.title}>
          {r.status.text}
        </span>
      </td>
      <td className="tc-mx__num" title={r.iter === null ? iterMissingTitle(r) : iterTitle(r)}>
        {r.iter === null ? '—' : `it${r.iter}`}
      </td>
      <td className="tc-mx__round">
        {r.ov?.hold ? (
          // ★2026-09-22（离线课列修正）+ ★M4（判据换成 hold）：被接管的课这列读「云机回传」
          // ——本地 13 步表的步骤（publish/等回传…）对操作员无读面意义，整段由云机执行。
          <span
            title={
              `云机整段执行中：最新回传 it${r.ov.offlineLastIter ?? '—'}（本机只收回传、` +
              '不跑这门课；PPO 派发被 hold 闸压住）'
            }
          >
            云机 it{r.ov.offlineLastIter ?? '—'}
          </span>
        ) : lq ? (
          <>
            <span title={stepTitle(lq)}>{lq.current || '—'}</span>
            <span className="tc-mx__todo" title={pendingTitle(lq)}>
              待办 {lq.pending.length}
            </span>
          </>
        ) : (
          <span title={queueMissingTitle()}>—</span>
        )}
      </td>
      <td className={`tc-mx__wait ${waitingClass(lq)}`} title={r.waiting.title}>
        {r.waiting.text}
      </td>
      <td className="tc-mx__num" title={r.queue.title}>
        {r.queue.text}
      </td>
      <td
        className={r.hold?.warn ? 'tc-mx__seg tc-mx__seg--stale' : 'tc-mx__seg'}
        title={r.hold?.title}
      >
        {r.hold?.text ?? '—'}
      </td>
      {onAction ? (
        <td className="tc-mx__ops">
          {/* ★M4：旧的三颗模式钮（切离线 / 切换成在线 / 交还自动池）与两个漂移徽标已随
              「课程不再分在线/离线」退役——「这门课归谁」今天只有一个真源：hub 的**接管**
              （`hold`）。人唯一能干预的动作是「强制解除接管」（`release_hold=1`）：立撤租墓碑
              + 清 hold ⇒ 本机下一轮恢复采样、协作派发立即恢复、新自主盘可当场 claim。
              只在真有接管时才画（`r.canReleaseHold`）：hub 离线 / 它不认识这门课 / 本来就没
              接管时点下去一定 404/409——画一个一定失败的按钮是假承诺（能力边界，与「只读
              不禁用」那条不同）。 */}
          {r.canReleaseHold ? (
            <span className="tc-mx__opgroup" role="group" aria-label="hub：强制解除接管">
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                aria-label={`hub：强制解除接管 ${r.course}`}
                title={
                  '强制解除接管（`release_hold=1`）：立撤租墓碑 + 清 hold——本机在下一轮边界' +
                  '恢复采样，协作派发立即恢复，新自主盘可当场 claim（不必等 15 分钟静默阈）。' +
                  '若云机其实还在跑，它下次打点会收 409 且其产物只归档（有界，不会双写权重）。'
                }
                onClick={() => void onAction('releaseCourseHold', { course: r.course })}
              >
                强制解除接管
              </button>
            </span>
          ) : null}
          {pause ? (
            <>
              <span className="tc-mx__opsep" aria-hidden="true" />
              <span className="tc-mx__opgroup" role="group" aria-label={`本地：${pause.label}`}>
                <button
                  type="button"
                  className="tc-btn tc-btn--sm"
                  aria-label={`本地：${pause.label} ${r.course}`}
                  title={pause.title}
                  onClick={() =>
                    void onAction('setCoursePaused', {
                      course: r.course,
                      paused: !lq!.pausedIntent,
                    })
                  }
                >
                  {pause.label}
                </button>
              </span>
              {/* 事实徽标：开关改的是**意图文件**，能不能生效由训练进程决定 ⇒ 诚实区分
                  「已暂停」与「待生效」（只看意图会骗人，只看事实则点完没反馈）。 */}
              {pause.badge ? (
                <span
                  className={`tc-mx__pausebadge tc-badge tc-badge--${pause.badge.tone}`}
                  title={pause.title}
                >
                  {pause.badge.text}
                </span>
              ) : null}
            </>
          ) : null}
          {/* ★2026-09-22 改版：任务包能力（导出/导入训练结果）下沉到行内操作列——「任务包」
              独立面板已从首页下线（无操作通道时不渲染，与其余行内动作同判据）。
              ★2026-09-23（用户指令）：判据从「hub 标离线 ∨ 意图离线」再放宽到 **在训**
              （`r.bundleOps` = `rowTraining`，★M4）——任何一门在训课都可能被自主 worker 领走
              （claim 遇缺包时 hub 会请控制台导包，见 `pending_export`），所以导包对每门在训课
              都是合法动作（与 hub 此刻派不派活正交：包就是给云机用的）。 */}
          {r.bundleOps ? <BundleRowActions course={r.course} /> : null}
        </td>
      ) : null}
    </tr>
  )
}

/** 指针悬停：说清它是「下一轮要跑的 it」以及**来自哪一侧**（被接管的课 = 云机回传指针）。 */
function iterTitle(r: CourseMatrixRow): string {
  if (r.iterSource === 'hold')
    return (
      `回传指针 it${r.iter} —— 云机回传的最新段内 it（接管中：hub 只收回传、不实时派发；` +
      '本机「下一轮」队列指针对这一段无读面意义，见 /metrics 的账本尾行）'
    )
  const src = r.iterSource === 'queue' ? '训练侧队列（下一轮要跑）' : 'hub 侧账本尾行'
  const cross = r.iterSource === 'queue' ? '；hub 侧账本尾行另见 /metrics' : ''
  return `账本指针 it${r.iter} —— 来自${src}${cross}`
}

/** 两侧都没给出指针：**说清是不知道**，不是 0。 */
function iterMissingTitle(r: CourseMatrixRow): string {
  // ★M4：被接管的课还没有回传时，指针是**接管维度**的不可知——本机「下一轮」指针这一段
  // 没有读面意义（本机不跑这门课），所以不能拿它充数（见「接管」列）。
  if (r.ov?.hold)
    return '接管中但还没有段内回传：云机回传的最新 it 未知（本机「下一轮」指针这一段不适用）'
  return r.lq || r.ov
    ? '没有账本指针：这门课还没有 iteration 事件（未开训 / 账本还没结算过这一轮）'
    : '两侧都读不到——指针不可知'
}

function queueMissingTitle(): string {
  return '训练侧只读视图未读——本轮步骤与待办不可知（不是「无待办」）'
}
