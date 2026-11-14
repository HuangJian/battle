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
 *  **2026-11-14 集约化（用户）**：原来三块各一张全宽表（十几关 + 十二轮 ≈ 大半屏），现在：
 *   · 健康（每关达标、没触硬顶）⇒ 只留一块 badge，点「展开明细」才铺明细；
 *   · 有缺口 / 读数不全 ⇒ **默认展开**（问题不该藏在一次点击后面）；
 *   · 明细本身也从表改成格：逐关一格「关号 · 收到/达标线」（颜色 = 达标 / 缺口，悬停给局数·
 *     硬顶·est），最近轮次一行一轮。同样的信息，高度是原来的三分之一。
 *
 *  数据源只有一条、且只读：`stateView.metrics.iters[].rotation / .volume`（账本 `rotation` /
 *  `volume_stage_stats` 子字典，经 `server/iters.ts` 搬运）。**无任何轮换/分关读数 ⇒ 整块不渲染**
 *  （老课程页零变化）；缺哪一格就显 `—`，绝不编 0（0 在配额语义里是「真的没采到」）。
 *
 *  口径提醒：窗口关集 = 账本 `volume_stage_stats.stages` 的键（= 采集侧同用的那一份），
 *  控制台不重算窗口（它 import 不到 `nn-training/`，重算还需要课程配置 + rotate_seed）。
 */

import { useState } from 'preact/hooks'
import {
  type ConsoleStateView,
  type IterRow,
  healthText,
  rotationHealth,
  stageRangeText,
  stageTitle,
  volumeText,
  volumeTitle,
  windowText,
  windowTitle,
} from '../../view'
import { Badge } from '../../components/Pill'
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
  // 展开态：null = 跟着健康判据走（健康收起 / 不健康展开），点过就按用户的选择。
  const [open, setOpen] = useState<boolean | null>(null)

  const rows = stateView?.metrics.iters ?? []
  const rowsWithData = rows.filter(
    (r) => (r.rotation ?? null) !== null || (r.volume ?? null) !== null,
  )
  const latest = latestSampled(rows)
  if (!latest) return null

  const rot = latest.rotation ?? null
  const vol = latest.volume ?? null
  const health = rotationHealth(latest)
  const expanded = open ?? health !== 'ok'
  const recent = [...rowsWithData].sort((a, b) => b.iter - a.iter).slice(0, RECENT)

  return (
    <section className="tc-card tc-rot" aria-label="采集窗口">
      <SectionHeader
        title="采集窗口"
        count={rowsWithData.length}
        hint="逐轮采样子集（rotation_mode）：每轮只采窗口里的关，ceil(关数/每轮关数) 轮覆盖一遍。读数全部从账本搬运（不重算窗口）"
        note={
          <>
            {' '}
            <Badge
              tone={health === 'ok' ? 'g' : health === 'gap' ? 'y' : 'gray'}
              title="健康 = 逐关账在手、每关都到达标线、没触单关局数硬顶。有缺口或读数不全时明细默认展开"
            >
              {healthText(latest)}
            </Badge>{' '}
            {rot ? (
              <>
                <Badge tone="a">轮换 {rot.mode}</Badge>{' '}
                <Badge tone="gray">
                  窗口 {rot.window.length}/{rot.all} 关
                </Badge>
              </>
            ) : (
              <Badge tone="gray" title="未开轮换：每轮采 --stages 全集（关集 = 账本分关账的键）">
                全关采集
              </Badge>
            )}
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
        }
        actions={
          <button
            type="button"
            className="tc-link"
            aria-expanded={expanded}
            title={expanded ? '收起逐关明细与最近轮次' : '展开逐关明细与最近轮次'}
            onClick={() => setOpen(!expanded)}
          >
            {expanded ? '收起明细' : '展开明细'}
          </button>
        }
      />

      {expanded ? (
        <div className="tc-rot__body">
          <div className="tc-rot__line">
            <b>it{latest.iter}</b>
            <span className="tc-muted">{latest.time}</span>
            <span className="tc-muted">
              {rot ? `窗口关号 ${stageRangeText(rot.window)}` : '窗口 = 全集'}
              {vol ? ` · ${volumeText(vol)}` : ''}
              {vol?.quota !== null && vol?.quota !== undefined ? `（每关 ${vol.quota}）` : ''}
            </span>
          </div>

          {vol ? (
            <ul className="tc-rot__stages" aria-label="逐关采集账">
              {vol.stages.map((s) => (
                <li
                  className="tc-rot__st"
                  data-tone={s.quota === null ? 'na' : (s.shortfall ?? 0) > 0 ? 'gap' : 'ok'}
                  key={s.stage}
                  title={stageTitle(s)}
                >
                  <b>{s.stage}</b>
                  <span>
                    {s.collected}
                    <i>/{s.quota ?? '—'}</i>
                  </span>
                  {rot && rot.pinned.includes(s.stage) ? (
                    <span className="tc-rot__pin" title="pinned：每轮恒采">
                      pin
                    </span>
                  ) : null}
                  {s.shortfall ? <span className="tc-rot__gap">缺 {s.shortfall}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <div className="tc-caption tc-caption--flush">
              该轮账本没有分关采集账（本机腿才有 `volume_stage_stats`；云机/离线腿尚未搬进账本）。
            </div>
          )}

          {recent.length > 1 ? (
            <div className="tc-rot__recent">
              <span className="tc-small">
                <b>最近 {recent.length} 轮</b>{' '}
                <span className="tc-muted">（新→旧；判「每轮换到哪批关」与长期缺口）</span>
              </span>
              <ul className="tc-rot__rounds" aria-label="最近轮次窗口">
                {recent.map((r) => (
                  <li
                    className="tc-rot__rd"
                    key={r.iter}
                    title={`${windowTitle(r.rotation)}；${volumeTitle(r.volume)}`}
                  >
                    <b>it{r.iter}</b>
                    <span>{windowText(r.rotation)}</span>
                    <span>{volumeText(r.volume)}</span>
                    {r.capped ? (
                      <Badge tone="y" title="有单关触局数硬顶而配额未满">
                        触硬顶
                      </Badge>
                    ) : (r.volume?.halted ?? null) ? (
                      <Badge tone="g" title="逐关配额全部达成，池里剩下的候选没派出去">
                        满额
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
