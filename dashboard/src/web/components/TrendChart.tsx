/** TrendChart.tsx — 轻量 SVG 走势图：轴 + 网格 + 折线 + 悬停十字准星 + 坐标提示。
 *
 *  ★ **SVG 属性一律用 SVG 自己的拼法**（2026-09-20 实测踩到，见下）。JSX 里写 camelCase 是
 *  React 的习惯，但 Preact 的两条路径不一致：
 *    · `preact-render-to-string`（SSR）会把 `stopColor` 归一成 `stop-color`；
 *    · 客户端 diff 在 SVG 命名空间下是 `setAttribute(name, value)` **原样照抄**（只修 xlink/sName），
 *      于是 `stopColor` 成了一个 SVG 不认识的属性 → 被忽略 → `stop-color` 回默认值 **黑**。
 *  症状（用户报告）：从其它页切回总览（Hero 重新挂载 = 客户端新建这些元素）时，趋势线与横轴
 *  之间的**面积块变黑**；而硬刷新（= 用 SSR 那些正确的属性）一切正常。
 *  所以：真 SVG 拼法是短横线的（stop-color / stroke-width / text-anchor / fill-opacity …）就写
 *  短横线；本身就是 camelCase 的（viewBox / preserveAspectRatio / gradientUnits）保持 camelCase。
 *  回归闸：`tests/web-style-discipline.test.ts` 扫 src/web 下全部 `.tsx`，禁 camelCase 拼法。
 *  支持双序列叠加（rollout 实线 + eval 橙线）：hover 同时提示两口径数值。
 *  数据侧由 sliceSeries 按范围档位截取后绘制；hover 状态纯客户端。
 *
 *  单序列也是它：数据源档位切到单边时调用方只传 `series`（不传 `series2`），
 *  「只看 eval」另用 `color` 把唯一那条线染成 eval 橙——同一张图承担三种档位，
 *  不再各写一个组件。 */

import { useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { sliceSeries, trendHoverIndex, type Series, type TrendRange } from '../view'

export interface TrendChartProps {
  /** 主序列（rollout）：全量时序（组件内部按 range 截取）。 */
  series: Series
  /** 叠加序列（eval）：与主序列共用 iter 对齐；缺值 = NaN 不画点。 */
  series2?: Series
  range: TrendRange
  fmt: (v: number | null) => string
  tone?: 'g' | 'y' | 'r'
  height?: number
  /** y 轴下界上限：实际下界 = min(dataMin, yFloor)。缺省贴 dataMin。
   *  击杀/道具传 0（基底锁 0）；胜率传 0.3（基底不得高于 30%）。 */
  yFloor?: number
  /** y 轴**强制**下界（胜局耗时等需要按数据量级缩放时用）：轴下界 = yMin，
   *  可高于 dataMin（裁掉下方空白/低点）。与 yFloor 同传时 yMin 优先。 */
  yMin?: number
  /** 主序列图例色（默认 accent）。 */
  color?: string
  /** 叠加序列（eval）色：固定桁色系。 */
  color2?: string
}

const VB_W = 260
const PAD_L = 38
const PAD_R = 6
const PAD_T = 6
const PAD_B = 16
/** 轴刻度统一字体（横/纵一致）。 */
const AXIS_FONT = { fontSize: '8', fill: 'var(--muted)' } as const
/** eval 线的口径色（**唯一出处**）：高饱和橙，与 accent 蓝 rollout 强对比；
 *  白描边保证压在面积上仍可读。导出理由：Hero 的「只看 eval」档位把 eval 提为主序列时，
 *  那条线也得是同一支色——色值的第二个字面量不该出现在面板里（口径色跨组件必须同源）。 */
export const COLOR_EVAL = 'var(--eval-line, #ea580c)'
const COLOR2_DEFAULT = COLOR_EVAL

function pathFrom(
  vals: number[],
  n: number,
  px: (i: number) => number,
  py: (v: number) => number,
): string {
  let d = ''
  let started = false
  for (let i = 0; i < n; i++) {
    const v = vals[i]
    if (!Number.isFinite(v)) {
      started = false
      continue
    }
    d += `${started ? 'L' : 'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)} `
    started = true
  }
  return d
}

export function TrendChart({
  series,
  series2,
  range,
  fmt,
  tone,
  height = 56,
  yFloor,
  yMin,
  color: colorProp,
  color2 = COLOR2_DEFAULT,
}: TrendChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const s = sliceSeries(series, range)
  const vals = s.vals
  const iters = s.iters
  const n = vals.length

  // 叠加序列按主序列 iters 对齐（eval 稀疏：缺轮 = NaN）。
  let vals2: number[] | null = null
  if (series2 && n > 0) {
    vals2 = iters.map((it) => {
      const i = series2.iters.indexOf(it)
      return i >= 0 ? series2.vals[i] : Number.NaN
    })
  }

  const valid = vals.filter(Number.isFinite)
  const valid2 = vals2 ? vals2.filter(Number.isFinite) : []
  if (valid.length === 0 && valid2.length === 0) {
    return (
      <div className="tc-trend" style={{ height }}>
        <span className="tc-muted tc-small">暂无数据</span>
      </div>
    )
  }

  const plotW = VB_W - PAD_L - PAD_R
  const plotH = height - PAD_T - PAD_B
  const all = [...valid, ...valid2]
  const dataMin = all.length ? Math.min(...all) : 0
  const dataMax = all.length ? Math.max(...all) : 1
  const min = yMin !== undefined ? yMin : yFloor !== undefined ? Math.min(dataMin, yFloor) : dataMin
  const max = dataMax > min ? dataMax : min + Math.max(1, Math.abs(min) * 0.05)
  const span = max - min

  const px = (i: number): number => PAD_L + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW)
  const py = (v: number): number =>
    PAD_T + (span === 0 ? plotH / 2 : (1 - (v - min) / span) * plotH)

  const line = pathFrom(vals, n, px, py)
  const line2 = vals2 ? pathFrom(vals2, n, px, py) : ''

  // 面积只铺主序列。
  let area = line
  const firstIdx = vals.findIndex(Number.isFinite)
  const lastIdx = vals.length - 1 - [...vals].reverse().findIndex(Number.isFinite)
  const baseY = PAD_T + plotH
  if (firstIdx >= 0 && lastIdx >= 0 && lastIdx > firstIdx) {
    area += `L${px(lastIdx).toFixed(1)} ${baseY.toFixed(1)} L${px(firstIdx).toFixed(1)} ${baseY.toFixed(1)} Z`
  }

  // 主序列色：显式色 > tone 语义色 > accent。显式色是「只看 eval」档位用的（唯一那条线
  // 是 eval ⇒ 染成桁色，图例色与口径对得上）。
  const color =
    colorProp ??
    (tone === 'g'
      ? 'var(--green)'
      : tone === 'y'
        ? 'var(--yellow)'
        : tone === 'r'
          ? 'var(--red)'
          : 'var(--accent)')

  const hx = hover != null ? px(hover) : null
  const hv = hover != null ? vals[hover] : null
  const hy = hv != null && Number.isFinite(hv) ? py(hv) : null
  const hv2 = hover != null && vals2 ? vals2[hover] : null
  const hy2 = hv2 != null && Number.isFinite(hv2) ? py(hv2) : null

  const onMove = (e: JSX.TargetedMouseEvent<SVGRectElement>) => {
    // 捕获 rect = plot 区（x=PAD_L, width=plotW）：屏幕比例 t∈[0,1] 直接映射下标。
    // 勿再按全 viewBox 宽换算后减 PAD_L（会在中段把选点推到鼠标左侧）。
    const rect = e.currentTarget.getBoundingClientRect()
    setHover(trendHoverIndex(e.clientX, rect.left, rect.width, n))
  }

  const tipParts: string[] = []
  if (hv != null && Number.isFinite(hv)) tipParts.push(`${series.label} ${fmt(hv)}`)
  if (vals2 && hv2 != null && Number.isFinite(hv2)) {
    tipParts.push(`${series2!.label} ${fmt(hv2)}`)
  }

  return (
    <div className="tc-trend" style={{ height }}>
      <svg
        className="tc-trend__svg"
        width="100%"
        height={height}
        viewBox={`0 0 ${VB_W} ${height}`}
        preserveAspectRatio="none"
        aria-label={`${series.label}${series2 ? ` / ${series2.label}` : ''} 走势`}
      >
        <defs>
          {/* `gradientUnits="userSpaceOnUse"` + 用户坐标系的 y1/y2：渐变不再依赖**面积路径的
             包围盒**。包围盒退化（恒定序列 ⇒ 面积零高）时 objectBoundingBox 的渐变没有可用的
              坐标系，浏览器会把它画成黑色——而「平坦的一段」在训练曲线里很常见。 */}
          <linearGradient
            id={`tg-${series.key}`}
            gradientUnits="userSpaceOnUse"
            x1="0"
            y1={PAD_T}
            x2="0"
            y2={PAD_T + plotH}
          >
            <stop offset="0%" stop-color={color} stop-opacity="0.18" />
            <stop offset="100%" stop-color={color} stop-opacity="0" />
          </linearGradient>
        </defs>

        {/* 网格 + Y 轴刻度 */}
        {[0, 0.5, 1].map((t) => {
          const y = PAD_T + plotH * (1 - t)
          const v = min + span * t
          return (
            <g key={t}>
              <line
                x1={PAD_L}
                y1={y.toFixed(1)}
                x2={VB_W - PAD_R}
                y2={y.toFixed(1)}
                stroke="var(--border)"
                stroke-width="1"
              />
              <text
                x={PAD_L - 4}
                y={(y + 3).toFixed(1)}
                text-anchor="end"
                font-size={AXIS_FONT.fontSize}
                fill={AXIS_FONT.fill}
              >
                {fmt(v)}
              </text>
            </g>
          )
        })}

        {/* X 轴刻度（首/中/末迭代） */}
        {[0, 0.5, 1].map((t) => {
          const i = Math.round(t * (n - 1))
          const x = px(i)
          return (
            <text
              key={i}
              x={x.toFixed(1)}
              y={height - 2}
              text-anchor="middle"
              font-size={AXIS_FONT.fontSize}
              fill={AXIS_FONT.fill}
            >
              {iters[i]}
            </text>
          )
        })}

        {/* 面积 + 主折线 + 叠加线（eval：白描边 + 紫实线 + 点，压在最上层） */}
        {area ? <path d={area} fill={`url(#tg-${series.key})`} /> : null}
        <path
          d={line}
          fill="none"
          stroke={color}
          stroke-width="1.8"
          stroke-linejoin="round"
          stroke-linecap="round"
        />
        {line2 ? (
          <>
            {/* 白描边：与 rollout 线/面积分离，eval 不再被淹没 */}
            <path
              d={line2}
              fill="none"
              stroke="#fff"
              stroke-width="3.6"
              stroke-linejoin="round"
              stroke-linecap="round"
              opacity="0.9"
            />
            <path
              d={line2}
              fill="none"
              stroke={color2}
              stroke-width="2.2"
              stroke-linejoin="round"
              stroke-linecap="round"
            />
            {/* 有效评估点打点：小圆点标位置，不遮 rollout 线 */}
            {vals2
              ? vals2.map((v, i) =>
                  Number.isFinite(v) ? (
                    <circle
                      key={i}
                      cx={px(i).toFixed(1)}
                      cy={py(v).toFixed(1)}
                      r="1.6"
                      fill={color2}
                    />
                  ) : null,
                )
              : null}
          </>
        ) : null}

        {/* 悬停十字准星 + 两点 */}
        {hx != null ? (
          <g>
            <line
              x1={hx.toFixed(1)}
              y1={PAD_T}
              x2={hx.toFixed(1)}
              y2={baseY}
              stroke={color}
              stroke-width="1"
              stroke-dasharray="2 2"
              opacity="0.5"
            />
            {hy != null ? (
              <>
                <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r="3" fill={color} />
                <circle
                  cx={hx.toFixed(1)}
                  cy={hy.toFixed(1)}
                  r="5.5"
                  fill={color}
                  fill-opacity="0.18"
                />
              </>
            ) : null}
            {hy2 != null ? (
              <>
                <circle cx={hx.toFixed(1)} cy={hy2.toFixed(1)} r="3" fill={color2} />
                <circle
                  cx={hx.toFixed(1)}
                  cy={hy2.toFixed(1)}
                  r="5.5"
                  fill={color2}
                  fill-opacity="0.18"
                />
              </>
            ) : null}
          </g>
        ) : null}

        {/* 悬停捕获层 */}
        <rect
          x={PAD_L}
          y={PAD_T}
          width={plotW}
          height={plotH}
          fill="transparent"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        />
      </svg>

      {/* 坐标提示（双口径并排；固定在图表顶部） */}
      {hx != null && tipParts.length > 0 ? (
        <div className="tc-trend__tip" style={{ left: `${(hx / VB_W) * 100}%` }}>
          <span className="tc-trend__tip-it">it{hover != null ? iters[hover] : ''}</span>
          <span className="tc-trend__tip-val">{tipParts.join(' · ')}</span>
        </div>
      ) : null}
    </div>
  )
}
