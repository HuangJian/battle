/**
 * paired-gate.ts — §14 预注册验收口径：两条 pinned 记分卡的配对差门。
 *
 * 输入：baseline 与 candidate 的 m1-eval HTML 记分卡（内嵌 const DATA = [...]，
 * 每关一行 winRate/60 局）。配对粒度 = 关（35 对，同 seed 集）。
 *
 * 判定（§14 / §14.1.1 / §0.3.1）：
 *   主门   配对差 ≥ +2pp 且 95% CI 下界 > 0（2100 局口径）
 *   canary 配对差方向 > 0（不要求显著 —— §0.3.1 type-II 降格口径）
 *   桶门   A（中路无钢，n=1 观察项）/ B（砖密集 ∧ ¬A）/ C（其余）逐桶 CI 下界 ≥ −1pp
 *          （非劣界；显著性只要求主门 + B 桶）
 *
 * 桶判定：A = detectCentralBreachRisk（等价复算：band 无钢 + 中央出口开放 + 中央 spawn）；
 *         B = 砖占比 ≥ 85.7 分位 ∧ ¬A；C = 其余。
 *
 * Usage:
 *   bun tools/sim/paired-gate.ts --baseline reports/godai-baseline-hard-35x60.html \
 *       --candidate tmp/goal-t9a-eval.html [--gate canary|main]
 *
 * Ledger 配对模式（plan/goal-nn-action.md §4.2，A0 出口默认口径）：
 *   bun tools/sim/paired-gate.ts --baseline-ledger a.html.ledger.jsonl \
 *       --candidate-ledger b.html.ledger.jsonl
 * 数据源 = m1-eval 逐局账本（每局一行 stage/seed/outcome），配对粒度 =
 * (stage, seed)（2100 对）——关级配对 35 对的独立二项噪声底把可测门推到
 * ≳2.4pp；(stage,seed) 级配对把功效提升一个数量级（§4.2 裁定默认走这条）。
 * 零效应功率冒烟：同一 pinned 基线跑两次互配，读 SD/SE/CI（A0 步骤 5）。
 */
import { existsSync, readFileSync } from 'node:fs'
import { STAGES } from '../../src/config/stages'

interface StageRow {
  name: string
  winRate: number
  winRatePct: number
}

function extractData(htmlPath: string): StageRow[] {
  const html = readFileSync(htmlPath, 'utf8')
  const m = /const DATA = (\[.*?\]);/s.exec(html)
  if (!m) throw new Error(`no DATA array in ${htmlPath}`)
  return JSON.parse(m[1]) as StageRow[]
}

import { World } from '../../src/game/World'
import { detectCentralBreachRisk } from '../../src/ai/god/stage-adapt'

function brickRatio(tiles: string[]): number {
  let bricks = 0
  let passable = 0
  for (const row of tiles) {
    for (const ch of row) {
      if (ch === 'b') bricks++
      if (ch === '.' || ch === 'b' || ch === 'f' || ch === 'i') passable++
    }
  }
  return passable > 0 ? bricks / passable : 0
}

function stageBuckets(): Map<string, 'A' | 'B' | 'C'> {
  const out = new Map<string, 'A' | 'B' | 'C'>()
  let rows0 = 0
  const rows = STAGES.map((s) => {
    const stage = s as unknown as { name?: string; id?: number; tiles: string[] }
    // 直接调用真函数（需 World 装载地形；35 关一次，工具内可接受）
    const w = new World()
    w.loadStageData(s as never, rows0++)
    return {
      name: stage.name ?? String(stage.id),
      tiles: stage.tiles,
      breach: detectCentralBreachRisk(w),
    }
  })
  const ratios = rows.map((r) => ({ name: r.name, ratio: brickRatio(r.tiles) }))
  // 85.7 分位：手册 §1.1 的原型二阈值（砖占比前 1/3）
  const sorted = [...ratios].map((r) => r.ratio).sort((a, b) => b - a)
  const threshold = sorted[Math.floor(sorted.length * (1 / 3))] ?? Infinity
  for (const r of rows) {
    const dense = (ratios.find((x) => x.name === r.name)?.ratio ?? 0) >= threshold
    out.set(r.name, r.breach ? 'A' : dense ? 'B' : 'C')
  }
  return out
}

/** Welch 配对统计：mean/std/se/CI（z=1.96）。 */
function pairedStats(diffs: number[]): { mean: number; se: number; lo: number; hi: number } {
  const n = diffs.length
  const mean = diffs.reduce((a, b) => a + b, 0) / n
  const variance = n > 1 ? diffs.reduce((a, d) => a + (d - mean) ** 2, 0) / (n - 1) : 0
  const se = Math.sqrt(variance / n)
  return { mean, se, lo: mean - 1.96 * se, hi: mean + 1.96 * se }
}

// ============================================================
// Ledger 配对模式（§4.2 (stage,seed) 级 2100 对）
// ============================================================

interface LedgerRow {
  wver: string
  stage: number
  seed: number
  ok: boolean
  outcome: string
  ticks: number
}

function loadLedger(path: string): Map<string, LedgerRow> {
  if (!existsSync(path)) {
    console.error(`[paired-gate] ledger not found: ${path}`)
    process.exit(2)
  }
  const out = new Map<string, LedgerRow>()
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line.trim()) continue
    try {
      const e = JSON.parse(line) as LedgerRow
      if (!e.ok || !Number.isInteger(e.stage) || !Number.isInteger(e.seed)) continue
      out.set(`${e.stage}:${e.seed}`, e) // 后写覆盖先读（错误重跑审计行生效）
    } catch {
      /* 跳过残行 */
    }
  }
  return out
}

function ledgerGateMain(): void {
  const args = process.argv.slice(2)
  let baselinePath = ''
  let candidatePath = ''
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--baseline-ledger') baselinePath = args[++i]
    else if (args[i] === '--candidate-ledger') candidatePath = args[++i]
  }
  const base = loadLedger(baselinePath)
  const cand = loadLedger(candidatePath)

  // (stage,seed) 配对
  const diffs: number[] = []
  const perStage = new Map<number, number[]>()
  let ticksMatched = 0
  let zeroDiff = 0
  for (const [key, b] of base) {
    const c = cand.get(key)
    if (!c) continue
    const stage = b.stage
    const d = (c.outcome === 'stage_clear' ? 1 : 0) - (b.outcome === 'stage_clear' ? 1 : 0)
    diffs.push(d)
    if (d === 0) zeroDiff++
    if (c.ticks === b.ticks) ticksMatched++
    const arr = perStage.get(stage) ?? []
    arr.push(d)
    perStage.set(stage, arr)
  }
  if (diffs.length === 0) {
    console.error('[paired-gate] no (stage,seed) pairs matched between ledgers')
    process.exit(2)
  }
  const st = pairedStats(diffs)
  console.log(
    `=== (stage,seed) 配对差（${diffs.length} 对；基线 ${baselinePath} / 候选 ${candidatePath}）===`,
  )
  console.log(
    `paired diff: ${(st.mean * 100).toFixed(2)}pp  SD ${(Math.sqrt(st.se * st.se * diffs.length) * 100).toFixed(2)}pp  ` +
      `SE ${(st.se * 100).toFixed(3)}pp  95% CI [${(st.lo * 100).toFixed(2)}, ${(st.hi * 100).toFixed(2)}]pp`,
  )
  console.log(
    `零效应指纹: 恒等对 ${zeroDiff}/${diffs.length}（100% = 静默回退嫌疑），ticks 全等对 ${ticksMatched}/${diffs.length}`,
  )
  // 可测门口径（§4.2 二选一之 1 的换算：观测均值 ≥ 2pp + 1.96·SE_实测）
  console.log(
    `实测功效口径: 关级配对需 ≳2.4pp 的可测门在本配对粒度下为 ` +
      `≥ ${(0.02 + 1.96 * st.se).toFixed(4)}（均值）才能 CI 下界 > 0`,
  )
  // 逐关聚合（与 HTML 关级口径对照）
  const stageRows = [...perStage.entries()]
    .map(([s, ds]) => ({ s, mean: ds.reduce((a, b) => a + b, 0) / ds.length, n: ds.length }))
    .sort((a, b) => a.s - b.s)
  const stageLevel = pairedStats(stageRows.map((r) => r.mean))
  console.log(
    `关级聚合（${stageRows.length} 关）: ${(stageLevel.mean * 100).toFixed(2)}pp ` +
      `SE ${(stageLevel.se * 100).toFixed(2)}pp（对照用）`,
  )
}

function main(): void {
  const args = process.argv.slice(2)
  // ledger 模式分流（§4.2）：--baseline-ledger / --candidate-ledger
  if (args.includes('--baseline-ledger') || args.includes('--candidate-ledger')) {
    ledgerGateMain()
    return
  }
  let baselinePath = ''
  let candidatePath = ''
  let gate = 'canary'
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--baseline') baselinePath = args[++i]
    else if (args[i] === '--candidate') candidatePath = args[++i]
    else if (args[i] === '--gate') gate = args[++i]
  }
  if (!baselinePath || !candidatePath) {
    console.error('[paired-gate] --baseline and --candidate required')
    process.exit(2)
  }
  const base = extractData(baselinePath)
  const cand = extractData(candidatePath)
  if (base.length !== cand.length) {
    console.error(`[paired-gate] stage count mismatch: ${base.length} vs ${cand.length}`)
    process.exit(2)
  }
  const buckets = stageBuckets()

  // 逐关配对
  const byName = new Map(cand.map((r) => [r.name, r]))
  const diffsAll: number[] = []
  const bucketDiffs: Record<'A' | 'B' | 'C', number[]> = { A: [], B: [], C: [] }
  const bucketBase: Record<'A' | 'B' | 'C', number[]> = { A: [], B: [], C: [] }
  let matched = 0
  for (const b of base) {
    const c = byName.get(b.name)
    if (!c) continue
    matched++
    const d = c.winRate - b.winRate
    diffsAll.push(d)
    const bk = buckets.get(b.name) ?? 'C'
    bucketDiffs[bk].push(d)
    bucketBase[bk].push(b.winRate)
  }
  if (matched === 0) {
    console.error('[paired-gate] no stage names matched — different stage sets?')
    process.exit(2)
  }

  const overall = pairedStats(diffsAll)
  console.log(`=== §14 配对差门（${matched} 关 × 60 局，基线 ${baselinePath}）===`)
  console.log(
    `overall paired diff: ${(overall.mean * 100).toFixed(2)}pp ` +
      `SE ${(overall.se * 100).toFixed(2)}pp ` +
      `95% CI [${(overall.lo * 100).toFixed(2)}, ${(overall.hi * 100).toFixed(2)}]pp`,
  )

  // 主门 / canary
  const isMain = gate === 'main'
  const mainPass = overall.mean >= 0.02 && overall.lo > 0
  const canaryPass = overall.mean > 0
  console.log(`gate=${gate}`)
  console.log(
    isMain
      ? `主门（≥2pp 且 CI 下界>0）: ${mainPass ? 'PASS ✅' : 'FAIL ❌'}`
      : `canary（方向>0）: ${canaryPass ? 'PASS ✅' : 'FAIL ❌'}` +
          `（type-II 风险：方向为正但不显著时可选 700 局复核，§0.3.1）`,
  )

  // 桶门（非劣界 CI 下界 ≥ −1pp）
  console.log('\n桶门（非劣界：配对差 95% CI 下界 ≥ −1pp；A 桶 n=1 仅观察）:')
  for (const bk of ['A', 'B', 'C'] as const) {
    const ds = bucketDiffs[bk]
    if (ds.length === 0) {
      console.log(`  ${bk}: (empty)`)
      continue
    }
    const st = pairedStats(ds)
    const baseMean = bucketBase[bk].reduce((a, b) => a + b, 0) / bucketBase[bk].length
    const note = bk === 'A' ? '（观察项，不作门）' : st.lo >= -0.01 ? 'PASS' : 'FAIL'
    console.log(
      `  ${bk}（n=${ds.length} 关，基线均值 ${(baseMean * 100).toFixed(1)}%）: ` +
        `diff ${(st.mean * 100).toFixed(2)}pp, CI 下界 ${(st.lo * 100).toFixed(2)}pp — ${note}`,
    )
  }
  const bOk = pairedStats(bucketDiffs.B).lo >= -0.01
  const cOk = pairedStats(bucketDiffs.C).lo >= -0.01
  if (!isMain) {
    console.log(
      `\n桶门结论：B ${bOk ? 'PASS' : 'FAIL'} / C ${cOk ? 'PASS' : 'FAIL'}（拦截显著回退）`,
    )
  }
}

main()
