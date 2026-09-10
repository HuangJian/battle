/** verify-p4.ts — P4 回填硬验收（plan/rl-eval-system.md §10.5 trust-but-verify）。
 *
 * 体系必须自动复现 rl.progress §19/§20 三条手记结论，否则口径有 bug：
 *  ① p3-kb1 it15=55；② c4-kb1 平台 ~56；③ c4-margin it30=69 且胜局 tick 中位下降；
 *  ④ 43→37→34→47→50 在噪音带内（|Δ| < 1 MDD，不判趋势）。
 *
 * 用法（示例）：
 *   bun tools/training/evalboard/verify-p4.ts --course c4-margin --rung c4l1 \
 *     --expect 30:0.69 --series 20:0.43,22:0.37,25:0.34,27:0.47,30:0.50
 * 退出码 0 = 全过；非 0 = 口径/数据问题（逐项打印 Δ/MDD）。
 */

import { deriveMetrics } from './stats'
import { mddUnpaired } from './stats'
import { loadRows } from './store'
import { evalDataRoot } from '../console/evalboard'

function arg(argv: string[], k: string, dft = ''): string {
  const i = argv.indexOf(k)
  return i >= 0 ? (argv[i + 1] ?? dft) : dft
}

/** "it:rate,it:rate" → [ [it, rate] ]。 */
function parseSeries(s: string): Array<[number, number]> {
  if (!s) return []
  return s.split(',').map((p) => {
    const [a, b] = p.split(':')
    return [Number(a), Number(b)] as [number, number]
  })
}

if (import.meta.main) {
  const argv = process.argv.slice(2)
  const course = arg(argv, '--course')
  const rung = arg(argv, '--rung', 'c4l1')
  const expect = parseSeries(arg(argv, '--expect'))
  const series = parseSeries(arg(argv, '--series'))
  if (!course) {
    console.error('[verify-p4] --course 必需')
    process.exit(2)
  }
  const rows = loadRows(evalDataRoot()).filter((r) => r.course === course && r.rung === rung)
  const byIter = new Map<number, typeof rows>()
  for (const r of rows) {
    const arr = byIter.get(r.iter) ?? []
    arr.push(r)
    byIter.set(r.iter, arr)
  }
  let fail = 0
  for (const [it, want] of expect) {
    const rs = byIter.get(it) ?? []
    if (rs.length === 0) {
      console.log(`[verify-p4] FAIL it${it}: 无数据（回填未跑）`)
      fail++
      continue
    }
    const m = deriveMetrics(rs)
    const tol = mddUnpaired(rs.length, m.winRate, rs.length, want)
    const ok = Math.abs(m.winRate - want) <= tol
    console.log(
      `[verify-p4] ${ok ? 'PASS' : 'FAIL'} it${it}: win=${(m.winRate * 100).toFixed(1)}% 期望=${(want * 100).toFixed(0)}% n=${rs.length} MDD=${(tol * 100).toFixed(1)}pp`,
    )
    if (!ok) fail++
  }
  // 噪音带：相邻点 |Δ| < 1 MDD（非配对，不同段）⇒ 标噪音，不判趋势。
  for (let i = 1; i < series.length; i++) {
    const [ia, ra] = series[i - 1]
    const [ib, rb] = series[i]
    const A = byIter.get(ia) ?? []
    const B = byIter.get(ib) ?? []
    if (A.length === 0 || B.length === 0) {
      console.log(`[verify-p4] SKIP ${ia}→${ib}：缺数据`)
      continue
    }
    const ma = deriveMetrics(A)
    const mb = deriveMetrics(B)
    const tol = mddUnpaired(A.length, ma.winRate, B.length, mb.winRate)
    const noisy = Math.abs(mb.winRate - ma.winRate) < tol
    console.log(
      `[verify-p4] ${noisy ? 'NOISE' : 'SIGNAL'} it${ia}(${ra})→it${ib}(${rb}): 实测 Δ=${((mb.winRate - ma.winRate) * 100).toFixed(1)}pp MDD=${(tol * 100).toFixed(1)}pp`,
    )
  }
  // tick 中位下降（c4-margin ③ 后半）：--tick-down "itA,itB" 断言中位下降。
  const td = arg(argv, '--tick-down')
  if (td) {
    const [ia, ib] = td.split(',').map(Number)
    const ma = deriveMetrics(byIter.get(ia) ?? [])
    const mb = deriveMetrics(byIter.get(ib) ?? [])
    const ok =
      mb.winTickMedian !== null && ma.winTickMedian !== null && mb.winTickMedian < ma.winTickMedian
    console.log(
      `[verify-p4] ${ok ? 'PASS' : 'FAIL'} tick中位 it${ia}=${ma.winTickMedian} → it${ib}=${mb.winTickMedian}`,
    )
    if (!ok) fail++
  }
  process.exit(fail === 0 ? 0 : 1)
}
