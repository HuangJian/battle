/** KpiStrip.tsx — KPI 条：6 格「一眼看完」的关键读数（问题 C1）。
 *
 *  格子的内容（值 / 副读数 / 口径 / 档位）全在 `view/kpi.ts` 的纯函数 `kpiTiles` 里算好；
 *  本组件只做本层才能做的事：① `spark` 取值交给 `sparkPoints` 归一化成折线；② `to` 有值
 *  时把格子渲染成可点按钮（深链到承载它的那一页）。
 *
 *  **微型走势用 `sparkPoints`**（`view/spark.ts`）：那是仓库里既有的 sparkline 纯函数，
 *  此前在控制台**没有任何消费者**（死代码）——KPI 条是它的第一个真实用途，故这里不另写
 *  一套归一化。宽度/高度按格子尺寸给（52×18），字号与折线都在一条 grid 行里对齐。
 *
 *  不画的格子**就不画**：没有时序的读数（阶段 / 在训课程 / 算力 / 队列）不补一条平线——
 *  一条假的平线比没有走势更坏（它看起来像「一直没变」这个结论）。副读数占同一行位置，
 *  所以有走势没走势的格子高度一致。
 */

import { kpiToneClass, type KpiTile } from '../view/kpi'
import type { PageKey } from '../view/routes'
import { sparkPoints } from '../view/spark'

const SPARK_W = 52
const SPARK_H = 18

export interface KpiStripProps {
  tiles: KpiTile[]
  /** 点格子的深链（route 页走 pushState；省略 = 格子不可点）。 */
  onNavigate?: (page: PageKey) => void
}

/** 格内微型走势（`sparkPoints` 归一化；点太少画不出形状 → 不画）。 */
function Spark({ values }: { values: number[] }) {
  const sp = sparkPoints(values, SPARK_W, SPARK_H)
  if (!sp) return null
  return (
    <svg
      className="tc-kpi__spark"
      width={SPARK_W}
      height={SPARK_H}
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      aria-hidden="true"
    >
      <polyline
        points={sp.coords}
        fill="none"
        stroke={sp.color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={sp.lastX} cy={sp.lastY} r={2.2} fill={sp.color} />
    </svg>
  )
}

export function KpiStrip({ tiles, onNavigate }: KpiStripProps) {
  // 快照为空（首帧未到 / 视图缺）时不静默消失：整条按「读盘中」说明。
  if (tiles.length === 0) {
    return (
      <section className="tc-kpi" aria-label="关键指标">
        <div className="tc-kpi__tile tc-kpi__tile--pending" role="status">
          读盘中：尚未取到状态快照
        </div>
      </section>
    )
  }
  return (
    <section className="tc-kpi" aria-label="关键指标">
      {tiles.map((t) => {
        const body = (
          <>
            <span className="tc-kpi__label">{t.label}</span>
            <span className="tc-kpi__valrow">
              <b className={kpiToneClass(t.tone)}>{t.value}</b>
              {t.unit ? <span className="tc-kpi__unit">{t.unit}</span> : null}
            </span>
            <span className="tc-kpi__foot">
              <span className="tc-kpi__sub">{t.sub}</span>
              {t.spark && t.spark.length > 1 ? <Spark values={t.spark} /> : null}
            </span>
          </>
        )
        // 可点的格子必须是真按钮（键盘可达），不能是挂了 onClick 的 div。
        return t.to && onNavigate ? (
          <button
            key={t.id}
            type="button"
            id={`kpi-${t.id}`}
            className="tc-kpi__tile tc-kpi__tile--link"
            title={t.title}
            onClick={() => onNavigate(t.to!)}
          >
            {body}
          </button>
        ) : (
          <div key={t.id} id={`kpi-${t.id}`} className="tc-kpi__tile" title={t.title}>
            {body}
          </div>
        )
      })}
    </section>
  )
}
