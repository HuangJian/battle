/** WorkerContribution.tsx — 并行 worker 贡献度（plan/worker-contribution-view）。
 *
 *  · `variant="full"`：节点页（NodeStats 抽屉）主体——份额条列表 ⇄ 机器×课程矩阵 + 口径脚注；
 *  · `SamplingBrief` + `PpoBrief`：首页节点区的**两条对称缩略行**（2026-10-03 用户报障
 *    「rollout 节点与 ppo 节点混在一起，数据混乱」→ 2026-10-04「像采样合计一样缩略显示即可」）
 *    ——采样行在上、PPO 行在下（实线分隔），各带自己的标签与单位。两者挤在同一个按钮里、
 *    或把 PPO 铺成逐行带进度条的列表，都被用户否过。
 *
 *  硬口径（用户 2026-10-02 裁决 / plan §4）：
 *    · 两组**永不合并**（采样=局 / PPO=job）；样本名不合并——归属由来源决定，不由名字决定；
 *    · 缺数据渲染「—」（不是 0%）；份额分母组内自洽；
 *    · 单点依赖（某课只有一个身份有贡献）在矩阵列头显式标记。
 *  这里只做渲染：数字全部来自 `web/view/contribution.ts` 的纯函数（服务端同一份实现）。
 */

import { useState } from 'preact/hooks'
import { SegmentedControl } from '../../components/SegmentedControl'
import { StatusDot } from '../../components/StatusDot'
import {
  type ContributionBrief,
  type ContributionView,
  fmtCount,
  fmtShare,
  type PpoJobRef,
  type PpoWorkerLiveView,
} from '../../view'

export interface WorkerContributionProps {
  /** 节点页主体（两组并排的表 + 脚注）。首页的两条缩略行见 `SamplingBrief` / `PpoBrief`。 */
  variant: 'full'
  /** full 变体数据源（`/api/pool` 的 `contribution`）。 */
  contribution?: ContributionView | null
  /** 面板标题里的窗口标签（full）。 */
  windowLabel?: string
}

/** 份额条（纯 CSS：宽度按 5% 一档走 `data-w`，不写内联 style——视觉纪律闸）。 */
function RowBar({ share }: { share: number | null }) {
  const bucket = share == null ? 0 : Math.round(Math.max(0, Math.min(1, share)) * 20)
  return (
    <span className="tc-contrib-bar" aria-hidden="true">
      <span className="tc-contrib-barfill" data-w={bucket} />
    </span>
  )
}

/** 份额列表（两组各自成表）。 */
function ShareLists({ view }: { view: ContributionView }) {
  const { sampling, ppo } = view
  return (
    <div className="tc-contrib-groups">
      <div className="tc-contrib-group">
        <h4 className="tc-contrib-grouptitle">云端 PPO worker（job）</h4>
        {ppo.rows.length === 0 ? (
          <p className="tc-muted tc-small">
            窗口内无 PPO 归属数据（归属事件自本版起记账；在飞一列来自 hub 观测面）。
          </p>
        ) : (
          <table className="tc-contrib-table">
            <thead>
              <tr>
                <th>worker</th>
                <th>份额</th>
                <th className="tc-contrib-num">完成</th>
                <th className="tc-contrib-num">晚到·白算</th>
                <th className="tc-contrib-num">在飞</th>
              </tr>
            </thead>
            <tbody>
              {ppo.rows.map((r) => (
                <tr key={r.worker}>
                  <td className="tc-mono">{r.worker || '(未登记)'}</td>
                  <td className="tc-contrib-share">
                    <RowBar share={r.share} />
                    <b>{fmtShare(r.share)}</b>
                  </td>
                  <td className="tc-contrib-num">{fmtCount(r.done)}</td>
                  <td className="tc-contrib-num">
                    {r.rejected > 0 ? fmtCount(r.rejected) : <span className="tc-muted">0</span>}
                  </td>
                  <td className="tc-contrib-num">
                    {r.inflight > 0 ? fmtCount(r.inflight) : <span className="tc-muted">0</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="tc-contrib-group">
        <h4 className="tc-contrib-grouptitle">采样节点（局）</h4>
        {sampling.rows.length === 0 ? (
          <p className="tc-muted tc-small">窗口内无采样贡献。</p>
        ) : (
          <table className="tc-contrib-table">
            <thead>
              <tr>
                <th>节点</th>
                <th>份额</th>
                <th className="tc-contrib-num">rollout</th>
                <th className="tc-contrib-num">eval</th>
                <th className="tc-contrib-num">失败</th>
              </tr>
            </thead>
            <tbody>
              {sampling.rows.map((r) => (
                <tr key={r.id}>
                  <td className="tc-mono">{r.id}</td>
                  <td className="tc-contrib-share">
                    <RowBar share={r.share} />
                    <b>{fmtShare(r.share)}</b>
                  </td>
                  <td className="tc-contrib-num">
                    {r.split.rollout > 0 ? (
                      fmtCount(r.split.rollout)
                    ) : (
                      <span className="tc-muted">0</span>
                    )}
                  </td>
                  <td className="tc-contrib-num">
                    {r.split.eval > 0 ? (
                      fmtCount(r.split.eval)
                    ) : (
                      <span className="tc-muted">0</span>
                    )}
                  </td>
                  <td className="tc-contrib-num">
                    {r.split.fail > 0 ? (
                      fmtCount(r.split.fail)
                    ) : (
                      <span className="tc-muted">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

/** 课程矩阵（机器 × 课）：两组各自成行；单点依赖课在列头打 ◆。 */
function MatrixTable({ view }: { view: ContributionView }) {
  const m = view.matrix
  const sub = (courses: string[], c: string): string => (courses.includes(c) ? ' ◆' : '')
  const title = (c: string): string | undefined => {
    const tags: string[] = []
    if (m.soloSamplingCourses.includes(c)) tags.push('采样')
    if (m.soloPpoCourses.includes(c)) tags.push('PPO')
    return tags.length > 0 ? `单点依赖（该课只有一个身份有贡献）：${tags.join(' / ')}` : undefined
  }
  const span = m.courses.length + 2
  return (
    <div className="tc-contrib-matrixwrap">
      <table className="tc-contrib-table tc-contrib-table--matrix">
        <thead>
          <tr>
            <th>身份</th>
            {m.courses.map((c) => (
              <th key={c} className="tc-contrib-num" title={title(c)}>
                {c}
                {sub(m.soloSamplingCourses, c) || sub(m.soloPpoCourses, c) ? ' ◆' : ''}
              </th>
            ))}
            <th className="tc-contrib-num">合计</th>
          </tr>
        </thead>
        <tbody>
          <tr className="tc-contrib-grouphdr">
            <td colSpan={span}>采样节点（局：rollout/eval）</td>
          </tr>
          {m.samplingRows.length === 0 ? (
            <tr>
              <td colSpan={span} className="tc-muted">
                窗口内无采样贡献
              </td>
            </tr>
          ) : (
            m.samplingRows.map((r) => (
              <tr key={`s:${r.id}`}>
                <td className="tc-mono">{r.id}</td>
                {r.cells.map((cell, i) => (
                  <td key={i} className="tc-contrib-num">
                    {cell.split.rollout + cell.split.eval > 0 ? (
                      `${cell.split.rollout}/${cell.split.eval}`
                    ) : (
                      <span className="tc-muted">—</span>
                    )}
                    {cell.split.fail > 0 ? (
                      <span className="tc-muted tc-small"> +{cell.split.fail}败</span>
                    ) : null}
                  </td>
                ))}
                <td className="tc-contrib-num">{fmtCount(r.total)}</td>
              </tr>
            ))
          )}
          <tr className="tc-contrib-grouphdr">
            <td colSpan={span}>云端 PPO worker（job：完成/晚到）</td>
          </tr>
          {m.ppoRows.length === 0 ? (
            <tr>
              <td colSpan={span} className="tc-muted">
                窗口内无 PPO 归属数据
              </td>
            </tr>
          ) : (
            m.ppoRows.map((r) => (
              <tr key={`p:${r.worker}`}>
                <td className="tc-mono">{r.worker || '(未登记)'}</td>
                {r.cells.map((cell, i) => (
                  <td key={i} className="tc-contrib-num">
                    {cell.done + cell.rejected > 0 ? (
                      <>
                        {cell.done}
                        {cell.rejected > 0 ? (
                          <span className="tc-muted tc-small"> +{cell.rejected}晚</span>
                        ) : null}
                      </>
                    ) : (
                      <span className="tc-muted">—</span>
                    )}
                  </td>
                ))}
                <td className="tc-contrib-num">{fmtCount(r.total)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

/** 采样块的**收尾行**（首页）：采样份额合计 + top-N 节点份额。
 *
 *  为什么独立成行、且**只带采样一侧**（2026-10-03 用户报障：「rollout 节点 和 ppo 节点混在一起，
 *  数据混乱，难以区分」）：此前 PPO 与采样两组挤在**同一个按钮**里，两种单位、两种分母并排 ⇒
 *  身份与口径都分不开。现在份额归**采样块的末尾**（全部采样节点行都在它上面，它只是收尾合计），
 *  PPO 在自己那一行（`PpoBrief`）。两组仍**永不合并**（WC-plan §4.1b）——这里只是把「不合并」
 *  落到版式上：采样份额绝不与 PPO job 数出现在同一行。
 *
 *  这是**纯读数**（不可点）：唯一的深链入口在 PPO 子块的行尾，避免同一区出现两个「节点统计 ›」。 */
export function SamplingBrief({ brief }: { brief: ContributionBrief | null }) {
  if (!brief) return null
  const total = brief.sampling.total
  const parts = brief.sampling.top.map((s) => `${s.id} ${fmtShare(s.share)}`)
  return (
    <p
      className="tc-contrib-samplingbrief"
      title="采样份额 = 窗口内成功局（rollout + eval）占采样组内的比例；与 PPO job 数不同单位、分母各自独立（两组永不合并）"
    >
      <span className="tc-contrib-brieflabel">采样合计</span>
      <b>{total > 0 ? `${fmtCount(total)} 局` : '—'}</b>
      {parts.length > 0 ? <span className="tc-muted">{parts.join(' · ')}</span> : null}
    </p>
  )
}

/** PPO 缩略行（首页）：与 `SamplingBrief` **完全对称的一行**读数——`PPO 合计 858 job · 名字 份额 · …`。
 *
 *  用户 2026-10-04：「不需要百分比 progress bar，像「采样合计」一样缩略显示即可」⇒
 *  去掉**份额条**与**逐行列表**（那是节点页 `variant="full"` 的形态），首页只留合计 + 各台份额。
 *
 *  两类身份仍不混行：采样行在本行**上方**，各有自己的标签与单位（局 / job）；
 *  角色隔离（两组永不合并，WC-plan §4.1b）在这条版式上照旧成立——这里只是把 PPO 也压成一行，
 *  不是把它并进采样那一行。
 *
 *  份额仍**全列**（用户 2026-10-03「全部活跃 worker 都列」）；身份已按机器归并（`machineOf`）
 *  ⇒ 行数 = 机器数（单位数级），太长时由 `flex-wrap` 折行，不会撑破也不会挤掉数值。 */
export function PpoBrief({ brief }: { brief: ContributionBrief | null }) {
  if (!brief) return null
  const { ppo } = brief
  const parts = ppo.top.map((w) => `${w.worker || '(未登记)'} ${fmtShare(w.share)}`)
  return (
    <p
      className="tc-contrib-ppobrief"
      title="云端 worker 完成的 PPO job（单位：job；与采样局数不同单位、永不合并）；份额分母 = 本组内全体；身份按机器归并（剥 `:pid`）"
    >
      <span className="tc-contrib-brieflabel">PPO 合计</span>
      <b>{ppo.totalDone > 0 ? `${fmtCount(ppo.totalDone)} job` : '—'}</b>
      {parts.length > 0 ? <span className="tc-muted">{parts.join(' · ')}</span> : null}
    </p>
  )
}

/** 一段 job 引用的文本：`§<课>:it<N>`；`it` 不可知 ⇒ `it?`；课解析不出 ⇒ `?:it<N>`。
 *
 *  纯函数、导出以便单测（与 `fmtShare` 同规）——三段共用同一个形状，不各拼一遍。 */
export function jobRefText(r: PpoJobRef): string {
  return `§${r.course || '?'}:${r.it == null ? 'it?' : `it${r.it}`}`
}

/** 段（计算中 / 已下载 / 下载中）：**空段整段不渲染**（不是渲染一个空标题）。 */
function JobSeg({ label, refs, title }: { label: string; refs: PpoJobRef[]; title: string }) {
  if (refs.length === 0) return null
  return (
    <span className="tc-contrib-jobseg" title={title}>
      <b>{label}</b>
      {refs.map(jobRefText).join(',')}
    </span>
  )
}

/** 首页 PPO 区的**每台一行**（plan/dashboard-ppo-live-rows，2026-10-09）：
 *  「这几台机器现在正在算哪一轮、下一轮下好了没」。
 *
 *  与 `PpoBrief`（汇总行）**并存**：那一行回答「谁贡献过多少」（逐字不动：合计 + 各台份额），
 *  这一块回答「此刻在干什么」。两类的身份/单位口径仍各自成规（WC-plan §4.1b）。
 *
 *  三段的口径（全部据实，缺数据一律标不可知）：
 *    · **计算中** = hub 的活租约 ∧ 已 `POST /start`（`computing_ago` 非空）；
 *    · **已下载** = worker 上报的软持有（hub 完全看不到的那部分，是「下一轮已经躺好了」的证据）；
 *    · **下载中** = 认领后还在取包/下 payload ∪ worker 上报的软持有下载中（按 job 去重）。
 *  自主盘（持 live hold）也出行，轮次是**派生读数**（已补传产物，落后 ≤1 轮）——悬停里写明。 */
export function PpoWorkerRows({ live }: { live: PpoWorkerLiveView[] | null }) {
  if (!live || live.length === 0) return null
  return (
    <div className="tc-contrib-ppolive" aria-label="PPO worker 当前在做什么">
      {live.map((w) => (
        <p className="tc-contrib-ppolive--row" key={w.worker}>
          <StatusDot
            tone="ok"
            title="在工作中（hub 侧有活租约 / 持 live 接管 / 有已下好的软持有）"
          />
          <span className="tc-contrib-ppolive--name">{w.worker}</span>
          <span className="tc-muted">{fmtShare(w.share)}</span>
          <JobSeg
            label="计算中"
            refs={w.computing}
            title="hub 的活租约且已开算（`POST /jobs/{id}/start`）"
          />
          <JobSeg
            label="已下载"
            refs={w.held}
            title="worker 上报：软持有里已下好（下一轮开算时零下载；无租约，随时可能被挤掉）"
          />
          <JobSeg
            label="下载中"
            refs={w.dl}
            title="正在取包/下载 payload：hub 侧「认领未开算」∪ worker 上报的预取下载中"
          />
          {w.autonomous ? (
            <span
              className="tc-contrib-badge--auto"
              title={
                '自主盘：领整段任务包（hub 接管的 live 持有者）。轮次 = 已补传产物口径，' +
                '落后真实进度 ≤1 轮'
              }
            >
              自主
            </span>
          ) : null}
          {w.ageSec != null ? (
            <span className="tc-muted tc-small" title="预取状态上报龄（hub 侧 TTL 60s）">
              {`${w.ageSec.toFixed(0)}s前`}
            </span>
          ) : null}
        </p>
      ))}
    </div>
  )
}

/** 节点页主体（`variant="full"`）：两组并排的表 + 口径脚注。
 *  `variant` 只作 API 标记（首页的两个紧凑形态是独立组件），故不解构。 */
export function WorkerContribution({ contribution, windowLabel }: WorkerContributionProps) {
  const [mode, setMode] = useState<'list' | 'matrix'>('list')

  if (!contribution) return null
  return (
    <section className="tc-contrib" aria-label="worker 贡献度">
      <div className="tc-toolbar tc-toolbar--flush">
        <b>Worker 贡献度</b>
        <span className="tc-muted tc-small">
          窗口「{windowLabel ?? '—'}」· 采样（局）与 PPO（job）分列，永不合并
        </span>
        <span className="tc-push-right">
          <SegmentedControl<'list' | 'matrix'>
            value={mode}
            options={[
              { value: 'list', label: '份额列表' },
              { value: 'matrix', label: '课程矩阵' },
            ]}
            onChange={setMode}
            ariaLabel="贡献度视图"
          />
        </span>
      </div>
      {mode === 'list' ? <ShareLists view={contribution} /> : <MatrixTable view={contribution} />}
      <p className="tc-caption tc-caption--flush">
        口径：采样 = 窗口内成功局（rollout / eval 分列；失败单列）；PPO = 训练侧落位确认的完成
        job（分母组内自洽）；「晚到·白算」= 结果已落地/重复写入被 409 拒（机时已花），三条 409
        路径都记账、自本版起算；「在飞」= hub 当前活租约。归属由来源决定（同名不合并），时间 =
        训练机本地时。数据：{contribution.samplingSources.flows} 个训练流 +{' '}
        {contribution.ppoSources.ledgers} 份课程账本。
        {contribution.samplingSources.truncated ? ' ⚠ 有训练流只读了尾部，窗口外统计偏低。' : ''}
        {contribution.samplingSources.rollingTruncated
          ? ' ⚠ 有节点滚动窗事件环触顶被截断（近 2 小时数字偏低）。'
          : ''}
      </p>
    </section>
  )
}
