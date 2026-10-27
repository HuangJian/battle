/** StageRotationPanel.tsx — 「采集窗口」面板（`/metrics` 页顶部）：每轮采哪批关、各关进度多少。
 *
 *  为什么要有这一屏（2026-10-28 用户口径「每轮窗口与各关进度可见」，plan/it-stage-ratation §9.3）：
 *  窗口此前只活在进程日志的 `describe()` 一行里（滚走就没了）与账本里 `volume_stage_stats`
 *  的**键**上 ⇒ 开着轮换的课在控制台与全关课程长得一模一样：某关连着几轮没被采到、某关一直
 *  补不满，都要人翻 jsonl 才发现。这里把三件事摆在一起：
 *    ① 最新一轮的窗口读数（采了哪些关、多少关、达标多少、有没有触硬顶）；
 *    ② 该轮**逐关**采集账（收到 / 达标线 / 局数 / 硬顶 / est / 缺口）——判「是采不到还是没采」；
 *    ③ 最近若干轮的窗口一览（判「每轮换到哪批关、有没有长期缺口」——单点数字看不出趋势）。
 *
 *  数据源只有一条、且只读：`stateView.metrics.iters[].rotation / .volume`（账本 `rotation` /
 *  `volume_stage_stats` 子字典，经 `server/iters.ts` 搬运）。**无任何轮换/分关读数 ⇒ 整块不渲染**
 *  （老课程页零变化）；缺哪一格就显 `—`，绝不编 0（0 在配额语义里是「真的没采到」）。
 *
 *  口径提醒：窗口关集 = 账本 `volume_stage_stats.stages` 的键（= 采集侧同用的那一份），
 *  控制台不重算窗口（它 import 不到 `nn-training/`，重算还需要课程配置 + rotate_seed）。
 */

import {
  type ConsoleStateView,
  type IterRow,
  stageRangeText,
  volumeText,
  windowText,
} from '../../view'
import { Badge, Pill } from '../../components/Pill'
import { SectionHeader } from '../../components/SectionHeader'

/** 最新一轮**有采样范围读数**的行（iters 一般已按 iter 降序，但不假设——取最大者）。 */
function latestSampled(rows: IterRow[]): IterRow | null {
  let best: IterRow | null = null
  for (const r of rows) {
    if ((r.rotation ?? null) === null && (r.volume ?? null) === null) continue
    if (!best || r.iter > best.iter) best = r
  }
  return best
}

/** 面板只列最近这么多轮（「最近」的意义是判趋势，不是完整表——完整表在下面那张）。 */
const RECENT = 12

export function StageRotationPanel({ stateView }: { stateView: ConsoleStateView | null }) {
  const rows = stateView?.metrics.iters ?? []
  const rowsWithData = rows.filter(
    (r) => (r.rotation ?? null) !== null || (r.volume ?? null) !== null,
  )
  const latest = latestSampled(rows)
  if (!latest) return null
  const rot = latest.rotation ?? null
  const vol = latest.volume ?? null
  const recent = [...rowsWithData].sort((a, b) => b.iter - a.iter).slice(0, RECENT)

  return (
    <section className="tc-mx" aria-label="采集窗口">
      <SectionHeader
        title="采集窗口"
        count={rowsWithData.length}
        hint="逐轮采样子集（rotation_mode）：每轮只采窗口里的关，ceil(关数/每轮关数) 轮覆盖一遍。读数全部从账本搬运（不重算窗口）"
        note={
          rot ? (
            <>
              {' '}
              <Badge tone="a">轮换 {rot.mode}</Badge>{' '}
              <Badge tone="gray">
                窗口 {rot.window.length}/{rot.all} 关
              </Badge>
              {vol?.halted ? <Badge tone="g">满额收工</Badge> : null}
              {latest.capped ? (
                <Badge
                  tone="y"
                  title="有单关触局数硬顶而配额未满（缺口是「顶到了」，不是「还没补」）"
                >
                  触硬顶
                </Badge>
              ) : null}
            </>
          ) : (
            <>
              {' '}
              <Badge tone="gray" title="未开轮换：每轮采 --stages 全集（关集 = 账本分关账的键）">
                全关采集
              </Badge>
            </>
          )
        }
      />

      <div className="tc-line tc-mb-2">
        <span className="tc-small">
          <strong>it{latest.iter}</strong> <span className="tc-muted">{latest.time}</span>{' '}
          <span className="tc-muted tc-small">
            {rot ? `窗口关号 ${stageRangeText(rot.window)}` : '窗口 = 全集'}
            {vol ? ` · ${volumeText(vol)}` : ''}
            {vol?.quota !== null && vol?.quota !== undefined ? `（每关 ${vol.quota}）` : ''}
          </span>
        </span>
      </div>

      {vol ? (
        <table className="tc-wire__tbl tc-wire__tbl--full tc-mb-3">
          <thead>
            <tr>
              <th className="tc-small tc-muted">关</th>
              <th
                className="tc-small tc-muted"
                title="已结算 shard 的 nSamples 之和（与 target 同单位）"
              >
                收到
              </th>
              <th className="tc-small tc-muted" title="本关达标线 = ceil(target / 窗口关数)">
                达标线
              </th>
              <th className="tc-small tc-muted">局数</th>
              <th
                className="tc-small tc-muted"
                title="单关局数硬顶；空 = 不设（无估计的关不猜一个顶）"
              >
                硬顶
              </th>
              <th className="tc-small tc-muted" title="本关局均样本量估计（盘上历史派生）">
                est
              </th>
              <th className="tc-small tc-muted" title="缺口 = 达标线 − 收到；0 = 已达标">
                缺口
              </th>
            </tr>
          </thead>
          <tbody>
            {vol.stages.map((s) => (
              <tr key={s.stage}>
                <td className="tc-small">
                  {s.stage}
                  {rot && rot.pinned.includes(s.stage) ? (
                    <span className="tc-pill tc-pill--note" title="pinned：每轮恒采">
                      pin
                    </span>
                  ) : null}
                </td>
                <td className="tc-small">{s.collected}</td>
                <td className="tc-small">{s.quota ?? '—'}</td>
                <td className="tc-small">{s.games ?? '—'}</td>
                <td className="tc-small">{s.capGames ?? '—'}</td>
                <td className="tc-small">{s.estS ?? '—'}</td>
                <td className="tc-small">
                  {s.shortfall === null ? (
                    '—'
                  ) : s.shortfall > 0 ? (
                    <Badge tone="y">{s.shortfall}</Badge>
                  ) : (
                    '0'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="tc-caption tc-mb-3">
          该轮账本没有分关采集账（本机腿才有 `volume_stage_stats`；云机/离线腿尚未搬进账本）。
        </div>
      )}

      {recent.length > 1 ? (
        <div>
          <div className="tc-small tc-mb-1">
            <strong>最近 {recent.length} 轮</strong>{' '}
            <span className="tc-muted tc-small">（新→旧；判「每轮换到哪批关」与长期缺口）</span>
          </div>
          <table className="tc-wire__tbl tc-wire__tbl--full">
            <thead>
              <tr>
                <th className="tc-small tc-muted">it</th>
                <th className="tc-small tc-muted">窗口</th>
                <th className="tc-small tc-muted">关号</th>
                <th className="tc-small tc-muted">各关进度</th>
                <th className="tc-small tc-muted">备注</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r) => (
                <tr key={r.iter}>
                  <td className="tc-small">{r.iter}</td>
                  <td className="tc-small">{windowText(r.rotation)}</td>
                  <td className="tc-small tc-mono">
                    {r.rotation ? stageRangeText(r.rotation.window) : '—'}
                  </td>
                  <td className="tc-small">{volumeText(r.volume)}</td>
                  <td className="tc-small">
                    {r.capped ? (
                      <Pill tone="y" title="有单关触局数硬顶而配额未满">
                        触硬顶
                      </Pill>
                    ) : (r.volume?.halted ?? null) ? (
                      <Pill tone="g" title="逐关配额全部达成，池里剩下的候选没派出去">
                        满额
                      </Pill>
                    ) : (
                      ''
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
