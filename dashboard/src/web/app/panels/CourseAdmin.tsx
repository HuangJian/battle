/** CourseAdmin.tsx — 课程管理页（`/courses`）：**全部课程**一张表 + 封存区。
 *
 *  ## 这一页回答什么（与总览的课程矩阵分工）
 *
 *  总览那张矩阵只列**在训**的几门（盯着正在跑的那几门；未列的课只剩一个计数 chip）。
 *  于是三件事在旧界面上**没有读面**：
 *    ① 历史课占着多少盘、哪几门该封存（实测 `tmp/` 28 GB 里 27.3 GB 是 17 门课程目录）；
 *    ② `curricula/*.jsonc` 里声明了、却从没落盘过的课在哪；
 *    ③ 一门课从「开课」到「封存」的**全部动作**分别住在三个地方（侧栏选择器旁、课程矩阵行内、
 *       API/CLI）——封存尤其只有 API，界面上根本没有入口（2026-09-27 用户报障）。
 *  本页把三者收成一处：一张全课表，一行一门的动作都在行内。
 *
 *  ## 状态词表不另写（§4.2）
 *
 *  状态 / 在等什么 / 暂停 / 模式漂移 / 种类徽标全部走 `view/course-admin.ts` 的行构建，
 *  而它内部复用 `course-matrix` 的 `matrixStatus` / `waitingCell` / `pauseOp` —— 同一个状态
 *  只有一种说法。本文件只负责**排版与动作派发**。
 *
 *  ## 封存为什么是两步（预演 → 确认）
 *
 *  封存的顺序契约在 python 侧（建 → 校验 → 删），最贵的错误是「删了才发现没搬成」。所以：
 *  点「封存」先跑 `--dry-run`（零写零删），把**清单与字节账**摆出来（保留几件 / 删几件 /
 *  释放多少 MB），再点「确认封存」才带 `apply:true`。预演结果**不进 flash**（闪一下就没的
 *  数字没法据此做决定）——它留在行下方那块确认区里，直到确认或取消。
 *
 *  ## 只读语义
 *
 *  LAN 只读时动作按钮**照常渲染**（只读是服务端边界，不是涂灰按钮）；`onAction` 缺省 =
 *  这个渲染上下文没有动作通道（单测 / 无动作能力），此时不假装能控——与课程矩阵同判据。
 */

import { useState } from 'preact/hooks'
import {
  type ArchivedCourseView,
  type ConsoleStateView,
  type CourseAdminFilter,
  type CourseAdminRow,
  archiveOp,
  courseAdminFilterOptions,
  courseAdminFromState,
  courseAdminSummary,
  filterCourseAdminRows,
  lastWriteCell,
  matrixBadgeTone,
  matrixDotTone,
  pauseOp,
} from '../../view'
import type { ActionResult } from '../lib/api-client'
import { Collapsible } from '../../components/Collapsible'
import { Empty } from '../../components/Empty'
import { SectionHeader } from '../../components/SectionHeader'
import { SegmentedControl } from '../../components/SegmentedControl'
import { StatusDot } from '../../components/StatusDot'

/** 一次封存动作的结果（留在页面上供确认 / 看清失败，见文件头注）。 */
interface ArchivePreview {
  course: string
  /** 这一步是哪一步：预演（`--dry-run`）还是真封存（`apply:true`）。失败要说清栽在哪一步。 */
  phase: 'preview' | 'apply'
  ok: boolean
  message: string
  detail: string[]
}

export interface CourseAdminProps {
  /** 整页视图（缺省 = 还没拉到，显加载态）。 */
  stateView: ConsoleStateView | null
  /** 当前查看课程（高亮）。 */
  course: string
  onSelectCourse: (course: string) => void
  /** 打开「开课」弹窗（带课程级选项与封存起点选择器；弹窗住 app.tsx，这一页只请求）。 */
  onOpenCourseFor: (course: string) => void
  /** 动作通道。缺省 = 不渲染动作列（见文件头注）。 */
  onAction?: (act: string, body: Record<string, unknown>) => Promise<ActionResult>
}

/** `postAction` 把 `detail` 折进 `message` 的尾巴（flash 只用 message，那条通道需要它：
 *  「预演的数字」在 flash 里也得看得见）。而本页把 `detail` 单独渲染成清单 ⇒ 这里把折进去的
 *  那段摘掉，同一组数字不上屏两遍。只摘**明确折进去的尾巴**：没有 detail 的失败消息
 *  （拒绝原文可能带换行）一个字不动。 */
function splitDetail(r: ActionResult): { message: string; detail: string[] } {
  const detail = r.detail ?? []
  const tail = detail.length > 0 ? `\n${detail.join('\n')}` : ''
  const message = tail && r.message.endsWith(tail) ? r.message.slice(0, -tail.length) : r.message
  return { message, detail }
}

export function CourseAdmin({
  stateView,
  course,
  onSelectCourse,
  onOpenCourseFor,
  onAction,
}: CourseAdminProps) {
  // 「最后写入」是相对时间：取当下时刻一次（纯函数只收数字，见 view 层的纪律）。
  const nowSec = Math.floor(Date.now() / 1000)
  const [archOpen, setArchOpen] = useState(true)
  const [preview, setPreview] = useState<ArchivePreview | null>(null)
  const [busy, setBusy] = useState('')
  // 默认「有活体」：几十门历史课的档案条目（60+ 门 ladder/x*）会把在跑的那几行挤出首屏。
  const [filter, setFilter] = useState<CourseAdminFilter>('live')

  if (!stateView) {
    return (
      <div className="tc-panelbody">
        <div className="tc-loading">课程视图加载中…</div>
      </div>
    )
  }
  const view = courseAdminFromState(stateView, nowSec, course)
  const { rows, archived } = view
  const visible = filterCourseAdminRows(rows, filter)

  /** 封存第一步：预演（`--dry-run`，零写零删）——把清单与字节账摆出来再决定。 */
  const runPreview = async (c: string): Promise<void> => {
    if (!onAction) return
    setBusy(c)
    setPreview(null)
    const r = await onAction('archiveCourse', { course: c })
    setBusy('')
    setPreview({ course: c, phase: 'preview', ok: r.ok, ...splitDetail(r) })
  }

  /** 封存第二步：确认（`apply:true`）——真搬真删。
   *
   *  ★ 失败**不收起**这块：真封存的失败（409 在训 / 目录新鲜、500 校验失败、504 超时）正是最
   *  需要看清的，只丢给 flash 一闪而过就没了。留在页面上并写明是哪一步栽的；但**不给重试键**
   *  ——要再来一次得回行里重新点「封存」（重新走零写零删的预演），顺序契约不在界面上被绕过。
   *  成功才收起：课已进档案，下一帧它会从活体表里消失。 */
  const runApply = async (c: string): Promise<void> => {
    if (!onAction) return
    setBusy(c)
    const r = await onAction('archiveCourse', { course: c, apply: true })
    setBusy('')
    if (!r.ok) {
      setPreview({ course: c, phase: 'apply', ok: false, ...splitDetail(r) })
      return
    }
    setPreview(null)
  }

  return (
    <div className="tc-panelbody">
      {/* ══════════════════ 全部课程 ══════════════════ */}
      <section className="tc-ca" aria-label="课程管理">
        <SectionHeader
          title="课程"
          count={rows.length}
          hint={
            '全部课程（不只盯着在跑的那几门）：在训 / 已停 / 仅课程文件三类合一张表。' +
            '左起：状态（与总览课程矩阵同一个词表）· 开课标记 · 账本指针 · 在等什么 · 最后写入 · 动作。' +
            '「最后写入」= tmp/<课>/ 的 mtime——最久没动的排最后，那批正是该封存的'
          }
          note={<span className="tc-muted tc-small">{courseAdminSummary(view)}</span>}
          actions={
            <SegmentedControl<CourseAdminFilter>
              value={filter}
              options={courseAdminFilterOptions(view)}
              onChange={setFilter}
              ariaLabel="课程过滤"
            />
          }
        />
        {rows.length === 0 ? (
          <Empty
            kind="empty"
            reason="没有任何课程：tmp/ 下没有课程目录，curricula/ 里也没有课程文件"
          />
        ) : visible.length === 0 ? (
          <Empty
            kind="empty"
            reason={
              `当前过滤下没有课程（共 ${rows.length} 门）——切到「全部」看全量；` +
              '未落盘那些只有 curricula/<课>.jsonc，tmp/ 下还没有工作区'
            }
          />
        ) : (
          <div className="tc-tablewrap">
            <table className="tc-table tc-ca__table">
              <thead>
                <tr>
                  <th scope="col">课程</th>
                  <th scope="col">状态</th>
                  <th scope="col">开课标记</th>
                  <th scope="col">it</th>
                  <th scope="col">在等什么</th>
                  <th scope="col">最后写入</th>
                  {onAction ? <th scope="col">操作</th> : null}
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => (
                  <tr key={r.course} className={`tc-ca__row${r.viewing ? ' tc-ca__row--cur' : ''}`}>
                    <th scope="row" className="tc-ca__course">
                      <button
                        type="button"
                        className="tc-ca__pick"
                        aria-current={r.viewing ? 'true' : undefined}
                        title={r.viewing ? `${r.course}（当前查看）` : `切到查看 ${r.course}`}
                        onClick={() => onSelectCourse(r.course)}
                      >
                        <StatusDot tone={matrixDotTone(r.status.tone)} title={r.status.title} />
                        <b>{r.course}</b>
                      </button>
                      {r.kindBadge ? (
                        <span className="tc-badge tc-badge--a" title={`课程种类：${r.kindBadge}`}>
                          {r.kindBadge.toUpperCase()}
                        </span>
                      ) : null}
                    </th>
                    <td>
                      <span
                        className={`tc-badge tc-badge--${matrixBadgeTone(r.status.tone)}`}
                        title={r.status.title}
                      >
                        {r.status.text}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`tc-ca__mark tc-ca__mark--${r.enabled ? 'on' : 'off'}`}
                        title={
                          r.enabled
                            ? '已开课：tmp/<课>/training-enabled.txt 在——训练侧 enabled_courses 与 hub ' +
                              '_course_dir_live 用的是**同一个**闸'
                            : '未开课：没有开课标记（tmp/<课>/training-enabled.txt 不在）。' +
                              (r.tmp
                                ? '活体工作区还在（停过课 / 跑过没开过）'
                                : '连活体工作区都没有——只有课程文件')
                        }
                      >
                        {r.enabled ? '已开课' : r.tmp ? '未开课' : '未落盘'}
                      </span>
                    </td>
                    <td className="tc-ca__num" title={iterTitle(r)}>
                      {r.iter === null ? '—' : `it${r.iter}`}
                    </td>
                    <td className="tc-ca__wait" title={r.waiting.title}>
                      {r.waiting.text}
                    </td>
                    <td className="tc-ca__num" title={lastWriteCell(r, nowSec).title}>
                      {lastWriteCell(r, nowSec).text}
                    </td>
                    {onAction ? (
                      <td className="tc-ca__ops">
                        <RowOps
                          row={r}
                          busy={busy}
                          onSelectCourse={onSelectCourse}
                          onOpenCourseFor={onOpenCourseFor}
                          onAction={onAction}
                          onPreview={(c) => void runPreview(c)}
                        />
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 封存预演：结果留在页面上（见文件头注「为什么是两步」） */}
        {preview ? (
          <div
            className={`tc-ca__confirm${preview.ok ? '' : ' tc-ca__confirm--bad'}`}
            role="group"
            aria-label={`封存预演：${preview.course}`}
          >
            <div className="tc-ca__confirmhead">
              {preview.ok
                ? `封存预演（--dry-run，零写零删）：${preview.course}`
                : `${preview.phase === 'apply' ? '封存失败' : '封存预演失败'}：${preview.course}`}
            </div>
            <div className="tc-ca__confirmmsg">{preview.message}</div>
            {preview.detail.length > 0 ? (
              <ul className="tc-ca__confirmlist">
                {preview.detail.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            ) : null}
            <div className="tc-ca__confirmacts">
              {preview.ok ? (
                <button
                  type="button"
                  className="tc-btn tc-btn--sm tc-btn--danger"
                  disabled={busy === preview.course}
                  title={
                    '真封存：建档案（copy + 文本件 gzip）→ 逐件校验（解压后 sha256）→ 删清单里的' +
                    ' delete 项 → 移走空壳。校验任一件不符则**源目录一个字不动**并报错退出'
                  }
                  onClick={() => void runApply(preview.course)}
                >
                  {busy === preview.course ? '封存中…' : '确认封存'}
                </button>
              ) : null}
              <button type="button" className="tc-btn tc-btn--sm" onClick={() => setPreview(null)}>
                取消
              </button>
            </div>
          </div>
        ) : null}
      </section>

      {/* ══════════════════ 封存区 ══════════════════ */}
      <section className="tc-ca" aria-label="封存区">
        <SectionHeader
          title="封存区"
          count={archived.length}
          hint={
            '已封存课程（archive/courses/<课>/）——只读 archive-manifest.json，不扫盘、不解压。' +
            '封存课已从活体发现里排除：不进课程选择器、不进开课弹窗、hub 与训练侧都不再认它'
          }
          actions={
            archived.length > 0 ? (
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                aria-expanded={archOpen}
                onClick={() => setArchOpen((v) => !v)}
              >
                {archOpen ? '收起' : '展开'}
              </button>
            ) : null
          }
        />
        {archived.length === 0 ? (
          <Empty
            kind="empty"
            reason={
              '还没有封存过任何课程。在训课不得封存——先在「课程」表里停课，再点那一行的「封存」：' +
              '先跑预演（清单 + 字节账），确认后才真搬真删'
            }
          />
        ) : (
          <Collapsible collapsed={!archOpen}>
            <ul className="tc-ca__archlist">
              {archived.map((r) => (
                <ArchiveRow key={r.course} row={r} onSelectCourse={onSelectCourse} />
              ))}
            </ul>
          </Collapsible>
        )}
      </section>
    </div>
  )
}

/** 一行课程的指针悬停（与矩阵口径一致：说清这一格来自哪一侧）。 */
function iterTitle(r: CourseAdminRow): string {
  if (r.iter === null)
    return r.tmp
      ? '没有账本指针：这门课还没有 iteration 事件（未开训 / 账本还没结算过这一轮）'
      : '没有活体账本——只有课程文件（这一门还没跑过），或它是封存档案的终点 it'
  if (r.iterSource === 'hold')
    return `回传指针 it${r.iter} —— 云机回传的最新段内 it（接管中：hub 只收回传、不实时派发）`
  if (r.iterSource === 'queue') return `账本指针 it${r.iter} —— 训练侧队列（下一轮要跑）`
  return `账本指针 it${r.iter} —— hub 侧账本尾行`
}

/** 行内动作列：查看 · 开课/停课 · 暂停/恢复 · 强制解除接管 · 封存。 */
function RowOps({
  row: r,
  busy,
  onSelectCourse,
  onOpenCourseFor,
  onAction,
  onPreview,
}: {
  row: CourseAdminRow
  busy: string
  onSelectCourse: (course: string) => void
  onOpenCourseFor: (course: string) => void
  onAction: (act: string, body: Record<string, unknown>) => Promise<ActionResult>
  onPreview: (course: string) => void
}) {
  const pause = pauseOp(r.lq)
  const arch = archiveOp(r)
  return (
    <>
      {r.viewing ? null : (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`查看 ${r.course}`}
          title="把「当前查看课程」切到这一门（页面上的指标/日志/详情随之切换）"
          onClick={() => onSelectCourse(r.course)}
        >
          查看
        </button>
      )}
      {r.enabled ? (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`停课 ${r.course}`}
          title={
            '非破坏停课：删开课标记 + 写暂停意图（★M4：不再推 hub——那边没有模式可推）；' +
            '队列与账本一个字不动。★ 它**不杀**在飞的那一轮——要封存请等这一轮写完' +
            '（封存闸会拒新鲜目录）'
          }
          onClick={() => void onAction('stopCourse', { course: r.course })}
        >
          停课
        </button>
      ) : (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`开课 ${r.course}`}
          title="打开「开课」弹窗（rollout 位置 / 起点权重）——课程级选项随它一起下发"
          onClick={() => onOpenCourseFor(r.course)}
        >
          开课
        </button>
      )}
      {pause ? (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`本地：${pause.label} ${r.course}`}
          title={pause.title}
          onClick={() =>
            void onAction('setCoursePaused', {
              course: r.course,
              paused: !r.lq!.pausedIntent,
            })
          }
        >
          {pause.label}
        </button>
      ) : null}
      {/* ★M4：旧的三颗模式钮（切离线/切换成在线/交还自动池）随「课程不再分在线/离线」退役——
          人唯一能干预接管的地方是「强制解除接管」（`release_hold=1`：立墓碑 + 清 hold ⇒
          本机下一轮恢复采样、协作派发立即恢复、新自主盘可当场 claim）。与矩阵同行同一判据
          （`r.canReleaseHold`），只在真有接管时画——假承诺不如不画。 */}
      {r.canReleaseHold ? (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`hub：强制解除接管 ${r.course}`}
          title={
            '强制解除接管（`release_hold=1`）：立撤租墓碑 + 清 hold——本机在下一轮边界恢复采样，' +
            '协作派发立即恢复，新自主盘可当场 claim（不必等 15 分钟静默阈）。' +
            '若云机其实还在跑，它下次打点会收 409 且产物只归档（有界）。'
          }
          onClick={() => void onAction('releaseCourseHold', { course: r.course })}
        >
          强制解除接管
        </button>
      ) : null}
      {arch ? (
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          aria-label={`${arch.label} ${r.course}`}
          title={arch.title}
          disabled={busy === r.course}
          onClick={() => onPreview(r.course)}
        >
          {busy === r.course ? '预演中…' : arch.label}
        </button>
      ) : null}
    </>
  )
}

/** 封存档案一行：形态 / it 区间 / 可复算与否 / 关键轮 / 档案路径（只读 manifest 的字段）。 */
function ArchiveRow({
  row: r,
  onSelectCourse,
}: {
  row: CourseAdminRow
  onSelectCourse: (course: string) => void
}) {
  const a: ArchivedCourseView = r.archive!
  return (
    <li className="tc-ca__archrow">
      <span className="tc-ca__archname">
        <b>{a.course}</b>
        <span className="tc-muted tc-small">{a.archivedAt}</span>
      </span>
      <span
        className="tc-ca__archmeta"
        title={
          `形态 ${a.form}（A=旧 it<N>/dist/；B=新 it<N>/w<id>/；C=offline 回传）· ` +
          `it ${a.itRange[0]}–${a.itRange[1]}（终点 ${a.finalIt}）· codec ${a.codec} · ` +
          `档案 ${(a.bytesTotal / 1_000_000).toFixed(1)} MB（原名 ${(a.bytesRawTotal / 1_000_000).toFixed(1)} MB）` +
          (a.parent ? ` · 父臂 ${a.parent}` : '')
        }
      >
        {`形态 ${a.form} · it ${a.itRange[0]}–${a.itRange[1]} · `}
        <span className={a.shardsKept ? 'tc-ca__ok' : ''}>
          {a.shardsKept ? '含 shards（可复算）' : '无可复算 shards（只可比）'}
        </span>
      </span>
      <span
        className="tc-ca__archkeys"
        title={
          a.keyIters.length > 0
            ? `关键轮（opt/ckpt 保留点）：${a.keyIters.join(', ')}——可作新腿起点（开课弹窗的「起点权重」里选它）`
            : '没有解析出关键轮（档案里没有可作起点的权重件）'
        }
      >
        {a.keyIters.length > 0 ? `关键轮 ${a.keyIters.join(', ')}` : '无可作起点的关键轮'}
      </span>
      <span className="tc-ca__archpath" title={`档案目录：archive/courses/${a.course}/`}>
        {`archive/courses/${a.course}/`}
      </span>
      <button
        type="button"
        className="tc-btn tc-btn--sm"
        aria-label={`查看 ${a.course}`}
        title="把它设为当前查看课程（档案本身只读：指标/日志读面不覆盖封存课）"
        onClick={() => onSelectCourse(a.course)}
      >
        查看
      </button>
    </li>
  )
}
