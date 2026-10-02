/** WorkerContribution.tsx — 并行 worker 贡献度（plan/worker-contribution-view）。
 *
 *  · `variant="full"`：节点页（NodeStats 抽屉）主体——份额条列表 ⇄ 机器×课程矩阵 + 口径脚注；
 *  · `variant="compact"`：首页节点区**另起一行**缩略（两组 top-N + 组总量，点击跳节点页）。
 *
 *  硬口径（用户 2026-10-02 裁决 / plan §4）：
 *    · 两组**永不合并**（采样=局 / PPO=job）；样本名不合并——归属由来源决定，不由名字决定；
 *    · 缺数据渲染「—」（不是 0%）；份额分母组内自洽；
 *    · 单点依赖（某课只有一个身份有贡献）在矩阵列头显式标记。
 *  这里只做渲染：数字全部来自 `web/view/contribution.ts` 的纯函数（服务端同一份实现）。
 */

import { useState } from 'preact/hooks'
import { SegmentedControl } from '../../components/SegmentedControl'
import { type ContributionBrief, type ContributionView, fmtCount, fmtShare } from '../../view'

export interface WorkerContributionProps {
  variant: 'full' | 'compact'
  /** full 变体数据源（`/api/pool` 的 `contribution`）。 */
  contribution?: ContributionView | null
  /** compact 变体数据源（`/api/state` 的 `contributionBrief`，同一份聚合的裁剪）。 */
  brief?: ContributionBrief | null
  /** 面板标题里的窗口标签（full）。 */
  windowLabel?: string
  /** 点击缩略行 → 打开节点页（compact）。 */
  onMore?: () => void
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

export function WorkerContribution({
  variant,
  contribution,
  brief,
  windowLabel,
  onMore,
}: WorkerContributionProps) {
  const [mode, setMode] = useState<'list' | 'matrix'>('list')

  if (variant === 'compact') {
    if (!brief) return null
    const ppoTop = brief.ppo.top
      .map((w) => `${w.worker || '(未登记)'} ${fmtShare(w.share)}`)
      .join(' · ')
    const smpTop = brief.sampling.top.map((s) => `${s.id} ${fmtShare(s.share)}`).join(' · ')
    return (
      <button
        type="button"
        className="tc-contrib-compact"
        onClick={onMore}
        title="并行 worker 贡献度（点击打开节点统计看明细；采样=局、PPO=job，两组分列）"
        aria-label="worker 贡献度缩略，点击打开节点统计"
      >
        <span className="tc-contrib-compact__label">贡献</span>
        <span className="tc-contrib-compact__cell">
          PPO{' '}
          {brief.ppo.totalDone > 0
            ? `${fmtCount(brief.ppo.totalDone)} job${ppoTop ? ` · ${ppoTop}` : ''}`
            : '—'}
        </span>
        <span className="tc-contrib-compact__cell">
          采样{' '}
          {brief.sampling.total > 0
            ? `${fmtCount(brief.sampling.total)} 局${smpTop ? ` · ${smpTop}` : ''}`
            : '—'}
        </span>
        <span className="tc-contrib-compact__more">节点统计 ›</span>
      </button>
    )
  }

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
