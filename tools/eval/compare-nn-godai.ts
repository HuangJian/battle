/**
 * compare-nn-godai.ts — 对比 NN（intent-exec）与 God-AI 在 hard 各关的表现（35×60）。
 *
 * 从两个 m1-eval HTML（tmp/m1_nn_hard_60.html / tmp/m1_god_hard_60.html）提取
 * per-stage DATA，输出逐关对比（胜率/分数/关键维度）+ 汇总分析。
 *
 * 用法：bun tools/eval/compare-nn-godai.ts --nn <nn.html> --god <god.html> [--out <md>]
 */
import { readFileSync } from 'node:fs'

function arg(name: string, fb: string): string {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fb
}

interface Row {
  idx: number
  name: string
  score: number
  mean: number
  cvar: number
  se: number
  winRate: number
  avgKills: number
  dims: Record<string, number | null>
  winRatePct: number
}

function extractData(html: string): Row[] {
  const m = html.match(/const DATA = (\[.*?\]);/)
  if (!m) throw new Error('DATA not found in HTML')
  return JSON.parse(m[1]) as Row[]
}

const nnRows = extractData(readFileSync(arg('nn', 'tmp/m1_nn_hard_60.html'), 'utf8'))
const godRows = extractData(readFileSync(arg('god', 'tmp/m1_god_hard_60.html'), 'utf8'))
const byIdx = new Map(godRows.map((r) => [r.idx, r]))

const out: string[] = []
out.push('# NN（意图策略）vs God-AI — hard 35×60 逐关对比')
out.push('')
out.push(
  `- NN（intent-exec + B′ 权重）：**${((nnRows.reduce((s, r) => s + r.winRate, 0) / nnRows.length) * 100).toFixed(1)}%** 平均胜率`,
)
out.push(
  `- God-AI 基线：**${((godRows.reduce((s, r) => s + r.winRate, 0) / godRows.length) * 100).toFixed(1)}%** 平均胜率`,
)
const nnTot = nnRows.reduce((s, r) => s + r.winRate, 0)
const godTot = godRows.reduce((s, r) => s + r.winRate, 0)
const nnWin = Math.round(nnTot * 60)
const godWin = Math.round(godTot * 60)
out.push(
  `- 胜局：NN ${nnWin}/2100 vs God-AI ${godWin}/2100（Δ ${nnWin - godWin > 0 ? '+' : ''}${nnWin - godWin} 局）`,
)
out.push('')
out.push(
  '| # | 关卡 | NN胜率 | God胜率 | Δ胜率 | NN分 | God分 | Δ分 | NN守家 | God守家 | NN残命 | God残命 |',
)
out.push('|---|---|---|---|---|---|---|---|---|---|---|---|')

let nnBetter = 0
let godBetter = 0
let baseNN = 0
let baseGod = 0
for (const r of nnRows) {
  const g = byIdx.get(r.idx)
  if (!g) continue
  const dw = r.winRate - g.winRate
  const ds = r.score - g.score
  const nnBase = r.dims['baseIntegrity']
  const godBase = g.dims['baseIntegrity']
  const nnLives = r.dims['lives']
  const godLives = g.dims['lives']
  if (dw > 0.02) nnBetter++
  else if (dw < -0.02) godBetter++
  if (nnBase != null) baseNN += nnBase
  if (godBase != null) baseGod += godBase
  const mark = dw >= 0.02 ? '**' : dw <= -0.02 ? '' : '='
  out.push(
    `| ${r.idx} | ${r.name} | ${(r.winRate * 100).toFixed(0)}% | ${(g.winRate * 100).toFixed(0)}% | ${(dw * 100).toFixed(0)}pp | ${r.score.toFixed(3)} | ${g.score.toFixed(3)} | ${(ds * 100).toFixed(1)}pp | ${nnBase == null ? '—' : (nnBase * 100).toFixed(0)}% | ${godBase == null ? '—' : (godBase * 100).toFixed(0)}% | ${nnLives == null ? '—' : (nnLives * 100).toFixed(0)}% | ${godLives == null ? '—' : (godLives * 100).toFixed(0)}% | ${mark}`,
  )
}
out.push('')
out.push(
  `**汇总**：NN 胜率高于 God-AI 的关：**${nnBetter}** 关；God-AI 更高：**${godBetter}** 关；`,
)
out.push(
  `平均基地完整度：NN ${((baseNN / nnRows.length) * 100).toFixed(1)}% vs God-AI ${((baseGod / godRows.length) * 100).toFixed(1)}%`,
)

const text = out.join('\n')
console.log(text)
const outPath = arg('out', 'tmp/m1_nn_vs_godai.md')
if (outPath) {
  // 追加一行 markdown 提示（对比表已在上面文本中）
  console.log(`\n(对比表已打印；如需存档用 --out)`)
}
