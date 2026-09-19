#!/usr/bin/env bun
/**
 * m1-eval.ts — M1 sim evaluation of the NN player policy (plan §NN-M1).
 *
 * Runs the trained NN policy headlessly across stages × seeds in parallel
 * (Bun worker pool) and reports the stage-clear win rate against the ≥60%
 * gate. Concurrency is adaptive: the live worker count tracks system CPU load
 * (>90% −1, <85% +1, capped at the physical core count, floored at 1) so a
 * long run never oversubscribes the machine or stalls. Uses `tools/sim
 * --policy nn` plumbing: each worker runs
 * `runSimulation({ policy: 'nn' })`, which builds an `NNInput` (InputLike)
 * driven by the auto-discovered latest weights.
 *
 * Evaluation dimensions: every run is also scored with the God AI score-v7
 * model (tools/eval/godai-score.ts, plan/God-AI-Evaluation-Redesign.md §3) so
 * the eval emits, per stage, the full 11-dimension breakdown plus a risk-
 * adjusted v7 composite. This is what makes "0 kills vs 19 kills" and
 * "3 lives left vs 0" distinguishable in the report — the binary win/lose
 * signal alone cannot. Telemetry is collected read-only (AGENTS §2.1) and does
 * not change any outcome.
 *
 * After the sim, a sortable HTML scorecard is written to --out (default
 * tmp/m1_eval_scorecard.html), in the same style as
 * tmp/god-ai-hard-35stage-scorecard.html.
 *
 * Usage:
 *   bun tools/sim/m1-eval.ts --stages all --seeds 1-10 --difficulty hard
 *   bun tools/sim/m1-eval.ts --stages 1-5 --seeds 1-3 --policy nn
 *   bun tools/sim/m1-eval.ts --stages 1 --seeds 1 --policy nn   # 1-game sanity
 *   bun tools/sim/m1-eval.ts --stages all --seeds 1-12 --out tmp/m1_eval_scorecard.html
 *
 * 分派（dist）：节点通信 / 重试 / 权重下发 / rescan **只有 Python 一份实现**
 * （`nn-training/eval_m1_once.py` → `rl/batch_eval.BatchEvalRunner` + `dist_common`，
 * 即训练循环长期在用的那套）。本文件在 dist 路径上只做三件事：写 spec → 读回逐局行
 * → 打分/报告。`--policy intent-exec|goal|god` 可经 agent 分派；其余策略与 `--no-dist`
 * 走本机 worker 池（那是游戏引擎本身，不涉节点通信）。本机份额由配置决定
 * （`policy.evalLocalSlots` → `rl.local_slots`，`--dist-local` 可显式覆盖）。
 */

import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { AdaptiveSimWorkerPool, physicalCores } from './sim-pool'
import type { RunTelemetry } from './simulation-runner'
import type { SimTask, SimTaskResult } from './sim-worker'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import {
  scoreRun,
  aggregateStage,
  aggregateSuite,
  V7_SCORE_CONFIG,
  DEFAULT_AGGREGATION,
  type DimensionKey,
  type RunScore,
  type ScorableRun,
  type StageAggregate,
} from '../eval/godai-score'
import { writeScorecardHtml, type ScorecardRow, type ScorecardSuite } from './scorecard-html'
import { resolveLatestWeights } from '../../src/nn/weights'
import { join, resolve } from 'node:path'
import { flag } from '../lib/cli'
import { computeCodeHash } from '../agent/codehash-files'
import { configLocalSlots, provenanceNote } from '../lib/dist-node-gate'
import { requestNodeUpgrades, resolveUpgradeBranch, upgradeLogLines } from '../lib/node-upgrade'

/** 仓根（升级子进程 cwd 与 nn-py-safe.sh 的相对路径解析都用它）。 */
const REPO_ROOT = resolve(import.meta.dir, '..', '..')
import { BatchLedger, ledgerKey } from '../lib/batch-ledger'

/**
 * 分派链的 Python 入口（节点通信/重试/权重下发/rescan 的唯一实现，见文件头）。
 * 经 `nn-py-safe.sh`（AGENTS §0.1 规则 13：nn python 一律走官方解释器包装）。
 */
const PY_ENTRY = 'nn-training/eval_m1_once.py'
const PY_SAFE = 'tools/githook/nn-py-safe.sh'

/**
 * 可经 agent 分派的 policy（与 Python 侧 `eval_m1_once.DISPATCHABLE` 同集合）：
 * `nn` 需要先把 `--weights-dir` 解析成一个权重**文件**（见 `resolveNnWeights`），
 * intent-exec / goal 各自带权重文件，god 无权重语义。
 * 注：`goal-god` 自 2026-09-19 起不再分派（远端 goal 执行器需要 goal 权重桶，而它
 * 按 kind='none' 分派时远端必然缺权重——旧实现看似分派、实则不可用）。
 */
export const DIST_POLICIES = ['nn', 'intent-exec', 'goal', 'god'] as const

/** The 11 scored dimensions of the God AI score-v7 model (design §3). */
const DIM_KEYS: DimensionKey[] = [
  'progress', // π  kills / enemies
  'lives', // λ  lives remaining / start lives
  'baseIntegrity', // β  base alive + protection-ring survival
  'clearSpeed', // σ  how fast the stage was cleared (clears only)
  'tempo', // τ  kills per minute vs the stage reference
  'accuracy', // ε  kills per shot vs the stage reference
  'loot', // ρ  power-ups captured / power-ups offered
  'growth', // γ  final star level / max star level
  'baseSafety', // θ  1 − mean base pressure
  'openingTempo', // ω  how quickly the first kill landed
  'mobility', // μ  distinct cells visited (anti-oscillation)
]

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

/**
 * `--policy nn` 的权重**文件**解析：与 NNInput 的自动发现同一函数、同一默认目录
 * （`src/nn/weights.resolveLatestWeights`：先取最新的 `weights.<stamp>_ep*_val*.json`，
 * 无则回落 `weights.json`）。
 *
 * 为什么必须解析成文件：分派时节点上的 `export-eval-game` 要一个**文件路径**
 * （`--weights`），不能再靠「目录里自己找」。本机池仍按目录自动发现——同一解析函数
 * 保证两侧拿到的是同一个文件（否则分派/本地会因为「最新」的判定不同而跑不同权重）。
 */
export function resolveNnWeights(weightsDir: string | undefined, cwd: string): string | null {
  return resolveLatestWeights(weightsDir ?? join(cwd, 'nn-training', 'weights'))
}

function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [a, b] = spec.split('-').map(Number)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [Number(spec)]
}

async function main(): Promise<void> {
  const difficulty = arg('difficulty', 'hard')!
  const stageSpec = arg('stages', 'all')!
  const seedSpec = arg('seeds', '1-10')!
  const maxTicks = parseInt(arg('max-ticks', '36000')!, 10)
  const policy =
    (arg('policy', 'nn') as
      | 'god'
      | 'nn'
      | 'intent'
      | 'intent-exec'
      | 'intent-oracle'
      | 'goal'
      | 'goal-god') ?? 'nn'
  const weightsDir = arg('weights-dir')
  const intentWeights = arg('intent-weights')
  const goalWeights = arg('goal-weights')
  const promiseTicks = parseInt(arg('promise', '0')!, 10) // goal 承诺期 T（0 = 默认 240）
  const replan = parseInt(arg('replan', '0')!, 10) // M7① cadence 扫描（0 = 策略默认）
  const riskGated = arg('risk-gated') === '1' || arg('risk-gated') === 'true'
  const baseCadence = parseInt(arg('base-cadence', '0')!, 10)
  const dangerCadence = parseInt(arg('danger-cadence', '0')!, 10)
  // Max concurrency is the real physical core count (never oversubscribe the
  // machine). `--workers` may LOWER the cap for a conservative run but can never
  // exceed it. Live concurrency then tracks system CPU load via AdaptiveSimWorkerPool:
  //   load > 90% → −1 worker ; load < 85% → +1 worker ; floor = 1.
  // nn 策略的权重文件（分派要文件路径；本机池按目录自动发现——同一解析函数）
  const nnWeightsPath = policy === 'nn' ? resolveNnWeights(weightsDir, process.cwd()) : null
  const physical = physicalCores()
  const fixedWorkers = parseInt(arg('fixed-workers', '0')!, 10) // 0 = 自适应并发（默认）
  const workers =
    fixedWorkers > 0
      ? Math.min(fixedWorkers, physical)
      : Math.min(parseInt(arg('workers', String(physical))!, 10), physical)
  const outPath = arg('out', 'tmp/m1_eval_scorecard.html')!
  // v3.7 分布式分派：--dist-nodes <rl-config.json> 时，评估任务经 HTTP 派发到
  // rollout agent（mode=eval&kind=intent&policy=intent-exec），利用云机算力跑 NN 策略。
  // v4.0 auto-dist（用户指令 2026-08-29：远程节点随时可能上线，每批都要充分利用）：
  // 不传 --dist-nodes 时，若默认 rl-config.json 存在且未给 --no-dist，也走混合分派——
  // 死节点只有 ~5s ping 快速失败（并行），活节点即刻接管份额；纯本地用 --no-dist 显式关闭。
  // 布尔存在位用 flag()（位置无关）：本地 arg() 取「下一个 token」，把 --no-dist
  // 写在命令行末尾时取到 undefined → 静默忽略（2026-09-19 实测，m1-eval 同病）。
  //
  // 节点门（2026-09-19 补）：此前 tryActivate 只看 HTTP 200，**没有** codeHash/bun/
  // 能力位门 ⇒ 陈旧节点会被当可用算力（era 混算）。现在与 eval-course-ckpt /
  // rollout 同源（tools/lib/dist-node-gate.ts）：逐条跳过日志 + 收尾聚合 WARN +
  // provenance（`node:<id>`/`local` 计数）+ `--upgrade-nodes`（复用训练循环守卫）。
  const noDist = flag('no-dist')
  let distNodesPath = arg('dist-nodes', '')
  if (!distNodesPath && !noDist) {
    const defaultCfg = 'nn-training/rl-config.json'
    try {
      if (readFileSync(defaultCfg, 'utf8').includes('"nodes"')) distNodesPath = defaultCfg
    } catch {
      /* 无配置文件 → 纯本地 */
    }
  }
  const iterId = arg('iter-id', `m1eval-${Date.now()}`)!

  let stages
  let stageNames: string[]
  if (stageSpec === 'all') {
    stages = STAGES
    stageNames = STAGES.map((s) => s.name)
  } else if (stageSpec.includes('-')) {
    const [a, b] = stageSpec.split('-').map(Number)
    stages = STAGES.slice(a - 1, b)
    stageNames = stages.map((s) => s.name)
  } else {
    const idx = parseInt(stageSpec, 10) - 1
    stages = [STAGES[idx]]
    stageNames = [STAGES[idx].name]
  }
  const seeds = parseRange(seedSpec)

  process.stderr.write(
    `[m1-eval] policy=${policy} ${stages.length} stages × ${seeds.length} seeds = ${stages.length * seeds.length} games (workers=${workers}) -> ${outPath}\n`,
  )

  // Build tasks. `telemetry: true` makes the worker return RunTelemetry so the
  // score-v7 dimensions can be computed — read-only, outcome-preserving.
  const tasks: SimTask[] = []
  let id = 0
  for (let si = 0; si < stages.length; si++) {
    for (const seed of seeds) {
      tasks.push({
        id: id++,
        seed,
        stage: stages[si],
        difficulty,
        params: DEFAULT_GOD_AI_PARAMS as GodAIParams,
        maxTicks,
        stageIndex: si,
        policy,
        nnWeightsDir: weightsDir,
        intentWeightsDir: intentWeights,
        goalWeightsDir: goalWeights,
        promiseTicks: promiseTicks || undefined,
        replanEvery: replan || undefined,
        riskGated,
        baseCadence: baseCadence || undefined,
        dangerCadence: dangerCadence || undefined,
        telemetry: true,
      })
    }
  }

  // ---- v4.1 逐局账本：断点续跑 + 错误局重跑（rollout resume 机制的 TS 提取）----
  // wver 覆盖"影响结果的全部输入"（权重字节 / 无权重策略的占位）；权重变更或
  // 代码变更（--fresh）都会使旧账本条目不计入 done。
  // 账本只在**本机 worker 池**路径生效：dist 路径的断点台账是 Python run dir 里的
  // eval_log（同 wver 的 (stage,seed) 由 BatchEvalRunner 的 `_done_keys` 跳过），
  // 两套各自完整，不叠加（叠加只会让「谁在续跑」说不清）。
  const noFresh = arg('fresh') === undefined
  let wverBytes: Buffer | null = null
  if (policy === 'goal' && goalWeights) wverBytes = readFileSync(goalWeights)
  else if (policy === 'intent-exec' && intentWeights) wverBytes = readFileSync(intentWeights)
  else if (policy === 'god' || policy === 'goal-god') wverBytes = Buffer.from('{}')
  const wver = wverBytes ? createHash('sha256').update(wverBytes).digest('hex') : `local-${policy}`
  const ledger = noFresh && !distNodesPath ? new BatchLedger(`${outPath}.ledger.jsonl`, wver) : null
  if (distNodesPath)
    process.stderr.write(
      `[m1-eval] ledger: dist 路径由 Python run dir 断点（${outPath}.m1run；--fresh 清空）\n`,
    )
  const ledgerDone = ledger ? ledger.loadDone() : new Map()
  const milestone: Record<string, boolean> = {}

  // 按 id 归位的占位数组：未结算/账本跳过的任务必须是**空洞**（不是 undefined）——
  // 下面的 `results.map` 靠空洞跳过产生 JSON null 行；填成 undefined 会改成「默认值行」，
  // 静默改变 perGame/eval_log 的逐局口径。故用 `length` 预置而不能用 Array.from。
  const results: DistResult[] = []
  results.length = tasks.length
  /** error 局占位（dist 路径一轮跑完**不留空洞**——未结算即 error，交重跑循环/报告记账）。 */
  const failResult = (id: number): DistResult => ({
    id,
    ok: false,
    outcome: 'error',
    ticks: 0,
    killCount: 0,
    baseAlive: false,
  })
  const tasksTodo: SimTask[] = []
  for (const t of tasks) {
    const key = ledgerKey(t.stageIndex ?? 0, t.seed)
    const done = ledgerDone.get(key)
    if (done) {
      results[t.id] = {
        id: t.id,
        ok: done.ok,
        outcome: done.outcome,
        ticks: done.ticks,
        killCount: done.killCount,
        baseAlive: done.baseAlive,
      }
    } else {
      tasksTodo.push(t)
    }
  }
  if (ledgerDone.size > 0) {
    process.stderr.write(
      `[m1-eval] ledger resume: ${ledgerDone.size}/${tasks.length} already settled (wver=${wver.slice(0, 12)}) — running ${tasksTodo.length}\n`,
    )
  }

  /** 单局结算（含账本追加；results 按任务 id 归位）。 */
  const onSettleOne = (t: SimTask, res: DistResult): void => {
    results[t.id] = res
    ledger?.append({
      wver,
      stage: t.stageIndex ?? 0,
      seed: t.seed,
      ok: res.ok === true,
      outcome: res.outcome,
      ticks: res.ticks ?? 0,
      killCount: res.killCount,
      baseAlive: res.baseAlive === true,
    })
  }

  /** 一轮分派（断点续跑子集/重跑子集都走同一入口；fresh 只在首轮为真）。 */
  const runOnce = async (
    batchTasks: SimTask[],
    fresh: boolean,
  ): Promise<Record<string, number>> => {
    const base = results.filter(Boolean).length
    const progress = (d: number, tot: number): void => {
      reportProgress(base + d, tasks.length)
      // 里程碑快照（巡航口径）：每过 25% 落一份已结算局清单（机器可读）。
      const pct = (base + d) / tasks.length
      const mk = [0.25, 0.5, 0.75].find((m) => !milestone[String(m)] && pct >= m)
      if (mk !== undefined) {
        milestone[String(mk)] = true
        const rows = results.flatMap((r, i) =>
          r
            ? {
                stage: tasks[i].stageIndex,
                seed: tasks[i].seed,
                ok: r.ok,
                outcome: r.outcome,
                ticks: r.ticks,
                kills: r.killCount,
              }
            : [],
        )
        writeFileSync(
          `${outPath}.partial.json`,
          JSON.stringify({ done: base + d, total: tasks.length, rows }, null, 2),
        )
      }
      void tot
    }
    if (distNodesPath) {
      // 分派链 = Python（写 spec → BatchEvalRunner → 逐局行归位）；本机份额也在那边按配置决定。
      const src = await runPythonDist(
        distCtx,
        batchTasks,
        progress,
        (i, res) => onSettleOne(batchTasks[i], res),
        fresh,
      )
      // 不变量：一轮跑完不留空洞（未结算 = error 局，重跑循环只挑 ok===false 的）
      for (const t of batchTasks) if (results[t.id] === undefined) onSettleOne(t, failResult(t.id))
      return src
    }
    const pool = new AdaptiveSimWorkerPool(workers, 1)
    pool.setAdjustHook((desired, load) => {
      process.stderr.write(`[m1-eval] concurrency ${desired} (cpu ${load}%)\n`)
    })
    const sub = await pool.runAdaptive(batchTasks, progress, { fixed: fixedWorkers > 0 })
    for (const r of sub) {
      const t = batchTasks.find((x) => x.id === r.id)
      if (t) onSettleOne(t, r)
    }
    return {}
  }

  // Staged progress reporter (to stderr, so stdout stays clean JSON).
  // Prints ~20 waypoints + a final 100% line — no blind waiting.
  const totalGames = tasks.length
  const t0 = Date.now()
  const stepSize = Math.max(1, Math.ceil(totalGames / 20))
  let lastPrinted = 0
  const fmt = (ms: number): string => {
    const s = Math.round(ms / 1000)
    if (s < 60) return `${s}s`
    return `${Math.floor(s / 60)}m${s % 60}s`
  }
  const reportProgress = (doneN: number, totalN: number): void => {
    if (doneN < totalN && doneN - lastPrinted < stepSize) return
    lastPrinted = doneN
    const pct = Math.round((doneN / totalN) * 100)
    const elapsed = Date.now() - t0
    const rate = doneN / (elapsed / 1000) // games/sec
    const eta = rate > 0 ? (totalN - doneN) / rate : 0
    process.stderr.write(
      `[m1-eval] progress ${doneN}/${totalN} (${pct}%) elapsed ${fmt(elapsed)} eta ${fmt(eta * 1000)}\n`,
    )
  }

  // v4.0 auto-dist：策略可分发且配置存在 ⇒ 经 **Python** 分派（BatchEvalRunner）；
  // 否则纯本地（分派集合见文件头的 DIST_POLICIES）。
  if (distNodesPath && !(DIST_POLICIES as readonly string[]).includes(policy)) {
    process.stderr.write(
      `[m1-eval] policy ${policy} is not dispatchable (${DIST_POLICIES.join('|')}) — running local only\n`,
    )
    distNodesPath = ''
  }
  if (distNodesPath) {
    if (policy === 'intent-exec' && !intentWeights) {
      process.stderr.write(
        '[m1-eval] --dist-nodes --policy intent-exec requires --intent-weights\n',
      )
      process.exit(2)
    }
    if (policy === 'goal' && !goalWeights) {
      process.stderr.write('[m1-eval] --dist-nodes --policy goal requires --goal-weights\n')
      process.exit(2)
    }
    if (policy === 'nn') {
      // 解析不到就不派（本机池也会因同一原因失败，早报比半跑好）：目录里既没有
      // `weights.<stamp>_ep*_val*.json` 也没有 `weights.json`。
      const dirShown = weightsDir ?? join(process.cwd(), 'nn-training', 'weights')
      if (!nnWeightsPath) {
        process.stderr.write(
          `[m1-eval] --dist-nodes --policy nn: no weights in ${dirShown}` +
            ` (expect weights.<YYYYMMDD-HHMMSS>_ep*_val*.json or weights.json)\n`,
        )
        process.exit(2)
      }
      // 读数可追溯：这批跑的是哪个文件（目录里可能同时躺着几十份）
      process.stderr.write(`[m1-eval] nn weights: ${nnWeightsPath}\n`)
    }
  }
  // 本机并发：显式 `--dist-local` > 配置 `policy.evalLocalSlots` > `rl.local_slots` > --workers 全核。
  // 旧实现只认 --workers（= 物理核数）⇒ 配置里的「本机不参与」被静默覆盖（2026-09-19 实测）。
  const distLocalRaw = arg('dist-local')
  const distLocalParsed = distLocalRaw === undefined ? NaN : parseInt(distLocalRaw, 10)
  const distLocalCfg = distNodesPath ? configLocalSlots(distNodesPath) : { slots: null, source: '' }
  const distLocal =
    Number.isFinite(distLocalParsed) && distLocalParsed >= 0
      ? distLocalParsed
      : (distLocalCfg.slots ?? workers)
  if (distNodesPath)
    process.stderr.write(
      `[m1-eval] 本机槽位 distLocal=${distLocal}（来源：${
        Number.isFinite(distLocalParsed)
          ? '--dist-local'
          : distLocalCfg.slots !== null
            ? `配置 ${distLocalCfg.source}`
            : '物理核数（配置未约定）'
      }）\n`,
    ) // 分派权重文件（nn 用解析出的最新文件；god 无权重语义）
  const weightsArg =
    policy === 'goal'
      ? (goalWeights ?? '')
      : policy === 'intent-exec'
        ? (intentWeights ?? '')
        : policy === 'nn'
          ? (nnWeightsPath ?? '')
          : ''
  // ---- 分派上下文：spec 由 buildDistSpec（纯函数）从任务子集构造 ----
  // 路径一律绝对化：Python 子进程的 cwd 是仓根（REPO_ROOT），用户从别的目录敲命令时
  // 相对路径会在两侧指向不同文件（权重读不到 / 行写错地方）。
  const distCtx: DistCtx = {
    cfgPath: distNodesPath ?? '',
    policy,
    weights: weightsArg ? resolve(weightsArg) : '',
    difficulty,
    maxTicks,
    localSlots: distLocal,
    iterId,
    runDir: resolve(`${outPath}.m1run`),
    out: resolve(`${outPath}.m1run/rows.jsonl`),
  }
  // 错误局重跑（rollout clean-eval 的 CLEAN_EVAL_MAX_RETRY 语义）：错误局最多再跑 2 次。
  let todo = tasksTodo
  /** 逐局来源（`node:<id>` / `local`）——产物必须能回答「这批局谁跑的」。 */
  const bySrc: Record<string, number> = {}
  for (let attempt = 0; attempt <= 2 && todo.length > 0; attempt++) {
    if (attempt > 0) {
      process.stderr.write(
        `[m1-eval] error-game rerun attempt ${attempt}: ${todo.length} games
`,
      )
    }
    // fresh：只在首轮且用户给了 `--fresh` 时清 Python 台账（重跑子集绝不能清——
    // 清了就把首轮已结算的行一起丢掉，重跑会连成功局一起重跑）。
    const passSrc = await runOnce(todo, attempt === 0 && !noFresh)
    for (const [k, v] of Object.entries(passSrc)) bySrc[k] = (bySrc[k] ?? 0) + v
    todo = tasks.filter((t) => results[t.id] && results[t.id]!.ok === false)
  }

  // 收尾：来源注脚 + 可选节点升级（扫描/判 stale 全在 Python：dist_common.upgrade_stale_nodes）。
  // 注：个别节点环境上就是不支持远控升级（隧道后 agent 返 502 等）——那是环境事实，
  // Python 逐节点报告，不阻塞其他节点。
  if (distNodesPath) {
    const nodeIds = new Set(
      Object.keys(bySrc)
        .filter((k) => k.startsWith('node:'))
        .map((k) => k.slice(5)),
    )
    for (const l of provenanceNote('[m1-eval]', bySrc, {
      localCap: distLocal,
      usableNodes: nodeIds.size,
    }))
      process.stderr.write(`${l}\n`)
    if (flag('upgrade-nodes')) {
      const up = requestNodeUpgrades({
        repoRoot: REPO_ROOT,
        cfgPath: distNodesPath,
        expectedHash: computeCodeHash(),
        branch: resolveUpgradeBranch(REPO_ROOT),
      })
      for (const l of upgradeLogLines('[m1-eval]', up)) process.stderr.write(`${l}\n`)
    }
  }
  const simSeconds = (Date.now() - t0) / 1000
  process.stderr.write(
    `[m1-eval] progress ${totalGames}/${totalGames} (100%) done in ${fmt(Date.now() - t0)}\n`,
  )

  // Aggregate.
  const total = results.length
  let cleared = 0
  /**
   * 全灭局数（歼灭率口径，与 stage_clear 解耦）。
   * BONUS TIME 窗口（≈600 tick）内被 max-ticks 截断的局：outcome = max_ticks，
   * 但敌人已全灭 ⇒ 计入 `clearedAll`。方案 §2.1 的「全歼率」门以此为准。
   */
  let clearedAll = 0
  const outcomes: Record<string, number> = {}
  let totalKills = 0
  let totalTicks = 0
  let nTicks = 0
  // Per-stage accumulators for the score-v7 dimensions. Tasks are built
  // stage-major / seed-minor, so the stage index is floor(id / seeds.length).
  const perStage: Record<
    number,
    {
      total: number
      cleared: number
      /** 全灭局数（含 BONUS TIME 窗口被截断的局） */
      clearedAll: number
      kills: number
      dimSums: Record<string, number>
      dimCounts: Record<string, number>
      runScores: RunScore[]
    }
  > = {}
  for (const r of results) {
    const si = r.id !== undefined ? Math.floor(r.id / seeds.length) : 0
    if (!perStage[si]) {
      perStage[si] = {
        total: 0,
        cleared: 0,
        clearedAll: 0,
        kills: 0,
        dimSums: {},
        dimCounts: {},
        runScores: [],
      }
    }
    const acc = perStage[si]
    if (!r.ok) {
      outcomes['error'] = (outcomes['error'] ?? 0) + 1
      continue
    }
    outcomes[r.outcome] = (outcomes[r.outcome] ?? 0) + 1
    if (r.outcome === 'stage_clear') cleared++
    if (r.outcome === 'stage_clear' || r.cleared === true) clearedAll++
    totalKills += r.killCount
    if (typeof r.ticks === 'number') {
      totalTicks += r.ticks
      nTicks++
    }
    acc.total++
    if (r.outcome === 'stage_clear') acc.cleared++
    if (r.outcome === 'stage_clear' || r.cleared === true)
      acc.clearedAll = (acc.clearedAll ?? 0) + 1
    acc.kills += r.killCount

    // Score this run with the God AI score-v7 model. dist 路径带 agent 报告的原始
    // `scorable`（打分的完整输入；逐字段搬运会漂移）；本机 worker 池路径按
    // SimTaskResult 合成同一形状。
    const scorable: ScorableRun = r.scorable ?? {
      outcome: r.outcome,
      ticks: r.ticks,
      finalState: { killCount: r.killCount, lives: r.lives ?? 0, baseAlive: r.baseAlive },
      firstKillTick: r.firstKillTick,
      telemetry: r.telemetry,
    }
    const runScore = scoreRun(scorable, V7_SCORE_CONFIG)
    acc.runScores.push(runScore)
    for (const k of DIM_KEYS) {
      const v = runScore.dims[k]?.value
      if (v === null || v === undefined) continue
      acc.dimSums[k] = (acc.dimSums[k] ?? 0) + v
      acc.dimCounts[k] = (acc.dimCounts[k] ?? 0) + 1
    }
  }

  const winRate = total > 0 ? cleared / total : 0
  const gate = 0.6
  const pass = winRate >= gate

  // Per-stage breakdown: full risk-adjusted aggregate (score/mean/cvar/se/
  // winRate) + per-dimension means + average kills, in stage order.
  const stageAgg: StageAggregate[] = []
  const stageReports = Object.entries(perStage)
    .map(([siStr, acc]) => {
      const si = Number(siStr)
      const dims: Record<string, number> = {}
      for (const k of DIM_KEYS) {
        const c = acc.dimCounts[k] ?? 0
        dims[k] = c > 0 ? Number((acc.dimSums[k] / c).toFixed(3)) : 0
      }
      const sa = aggregateStage(
        stageNames[si] ?? `stage${si + 1}`,
        acc.runScores,
        DEFAULT_AGGREGATION,
      )
      stageAgg[si] = sa
      return {
        stage: stageNames[si] ?? `stage${si + 1}`,
        total: acc.total,
        cleared: acc.cleared,
        winRate: acc.total > 0 ? Number((acc.cleared / acc.total).toFixed(3)) : 0,
        clearRate: acc.total > 0 ? Number((acc.clearedAll / acc.total).toFixed(3)) : 0,
        avgKills: acc.total > 0 ? Number((acc.kills / acc.total).toFixed(2)) : 0,
        scoreV7: Number(sa.score.toFixed(4)),
        dims,
      }
    })
    .sort((a, b) => stageNames.indexOf(a.stage) - stageNames.indexOf(b.stage))

  // Top-level score-v7 suite (risk-adjusted headline, L4 aggregation).
  const suite = aggregateSuite(stageAgg.filter(Boolean), DEFAULT_AGGREGATION)
  const scoreV7 = {
    suite: Number(suite.suite.toFixed(4)),
    lcb: Number(suite.lcb.toFixed(4)),
    powerMean: Number(suite.powerMean.toFixed(4)),
    stageCvar: Number(suite.stageCvar.toFixed(4)),
    arithmeticMean: Number(suite.arithmeticMean.toFixed(4)),
    meanWinRate: Number(suite.meanWinRate.toFixed(4)),
    worstStage: suite.worstStage
      ? {
          name: suite.worstStage.name,
          score: Number(suite.worstStage.score.toFixed(4)),
          winRate: Number(suite.worstStage.winRate.toFixed(4)),
        }
      : null,
  }

  const report = {
    policy,
    difficulty,
    stages: stages.length,
    seeds: seeds.length,
    total,
    outcomes,
    winRate: Number(winRate.toFixed(4)),
    /** 全灭率（歼灭率口径）—— 方案 §2.1「全歼率」门以此判定 */
    clearRate: Number((total > 0 ? clearedAll / total : 0).toFixed(4)),
    gate,
    pass,
    totalKills,
    avgKills: total > 0 ? Number((totalKills / total).toFixed(2)) : 0,
    avgTicks: nTicks > 0 ? Math.round(totalTicks / nTicks) : 0,
    scoreV7,
    perStage: stageReports,
    /**
     * 逐局行（EvalBench D5(a) 入账用：eval_m1.py 逐行写 eval_log.jsonl）。
     * m1 跑的是 sim-worker（非 export-eval-game），天然缺 playerHits/enemyHits/
     * playerDamageTaken/stuckTicks/pu 分类型/score——ingest 侧进豁免清单，不伪造。
     */
    perGame: results.map((r, i) => ({
      stage: tasks[i]?.stageIndex ?? 0,
      seed: tasks[i]?.seed ?? 0,
      ok: r?.ok === true,
      outcome: r?.outcome ?? 'error',
      win: r?.outcome === 'stage_clear',
      cleared: r?.cleared === true || r?.outcome === 'stage_clear',
      ticks: r?.ticks ?? 0,
      kills: r?.killCount ?? 0,
      lives: r?.lives ?? null,
      firstKillTick: r?.firstKillTick ?? null,
      enemyTotal: r?.telemetry?.enemyTotal ?? null,
      playerDeaths: r?.telemetry?.playerDeaths ?? null,
      playerShots: r?.telemetry?.playerShots ?? null,
      powerUpsCollected: r?.telemetry?.powerUpsCollected ?? null,
      playerLevel: r?.telemetry?.finalPlayerLevel ?? null,
      cellsVisited: r?.telemetry?.cellsVisited ?? null,
    })),
  }

  console.log(JSON.stringify(report, null, 2))
  process.stderr.write(
    `\n[m1-eval] WIN RATE ${(winRate * 100).toFixed(1)}% (gate ${gate * 100}%) -> ${pass ? 'PASS' : 'FAIL'}\n`,
  )
  process.stderr.write(
    `[m1-eval] CLEAR RATE（全灭率） ${((total > 0 ? clearedAll / total : 0) * 100).toFixed(1)}% ` +
      `(${clearedAll}/${total}) —— 含 BONUS TIME 窗口内被 max-ticks 截断的局；方案 §2.1「全歼率」门以此判定\n`,
  )
  process.stderr.write(
    `[m1-eval] SCORE V7 suite=${scoreV7.suite} lcb=${scoreV7.lcb} meanWinRate=${scoreV7.meanWinRate}` +
      (scoreV7.worstStage
        ? ` worst=${scoreV7.worstStage.name}(${scoreV7.worstStage.winRate})\n`
        : '\n'),
  )

  // ---- HTML scorecard (mirrors tmp/god-ai-hard-35stage-scorecard.html) ----
  const htmlRows: ScorecardRow[] = stageReports.map((sr, i) => {
    const sa = stageAgg[stageNames.indexOf(sr.stage)]!
    return {
      idx: i + 1,
      name: sr.stage,
      score: sa.score,
      mean: sa.mean,
      cvar: sa.cvar,
      se: sa.se,
      winRate: sa.winRate,
      avgKills: sr.avgKills,
      dims: sr.dims,
    }
  })
  const htmlSuite: ScorecardSuite = {
    suite: suite.suite,
    lcb: suite.lcb,
    arithmeticMean: suite.arithmeticMean,
    meanWinRate: suite.meanWinRate,
    worstStage: suite.worstStage
      ? { name: suite.worstStage.name, winRate: suite.worstStage.winRate }
      : null,
  }
  try {
    const written = writeScorecardHtml(outPath, htmlRows, htmlSuite, {
      title: `NN 策略 关卡评分卡 — ${difficulty} 难度`,
      difficulty,
      stages: stages.length,
      seeds: seeds.length,
      maxTicks,
      simSeconds,
      note: `policy=${policy} · v7 宽带`,
      extraCols: [{ key: 'avgKills', label: '平均击杀', get: (r) => r.avgKills, digits: 2 }],
    })
    process.stderr.write(`[m1-eval] wrote HTML scorecard -> ${written}\n`)
  } catch (e) {
    process.stderr.write(`[m1-eval] HTML scorecard failed: ${(e as Error).message}\n`)
  }
}

// ---------------- 分派（dist）：spec → Python（BatchEvalRunner）→ 逐局行 ----------------
//
// 这里**只**做三件事：把任务子集翻成 spec、调 Python、把逐局行归位。节点 ping/门/
// 退避重试/rescan/失败停用/权重下发/wver 409/本机份额全在 `nn-training/eval_m1_once.py`
// → `rl/batch_eval.BatchEvalRunner` + `dist_common`（训练循环长期实战的那套）——本文件
// 不再有第二份实现（2026-09-19 用户裁定：Python 端已有这些机制，别再在 TS 里重建）。

/** 分派上下文（main 构造；runPythonDist 只读；测试直接构造）。 */
export interface DistCtx {
  cfgPath: string
  policy: string
  weights: string
  difficulty: string
  maxTicks: number
  localSlots: number
  iterId: string
  runDir: string
  out: string
}

/** Python 侧 spec 契约（`nn-training/eval_m1_once.py --spec`）。 */
export interface M1DistSpec {
  runDir: string
  out: string
  policy: string
  weights: string
  difficulty: string
  maxTicks: number
  distCfgPath: string
  localSlots: number
  iterId: string
  fresh: boolean
  windowSec: number
  taskTimeoutSec: number
  units: Array<{ stageId: number; seeds: number[] }>
}

/**
 * 任务子集 → spec（纯函数，可单测）。
 *
 * unit = 一个内置关（`stageId` = 关卡索引）+ 它的种子段；stage 升序保证行序稳定
 * （错误局重跑只换 tasks 子集，语料口径仍只有这一处）。
 */
export function buildDistSpec(ctx: DistCtx, tasks: SimTask[], fresh: boolean): M1DistSpec {
  const byStage = new Map<number, number[]>()
  for (const t of tasks) {
    const si = t.stageIndex ?? 0
    const arr = byStage.get(si)
    if (arr) arr.push(t.seed)
    else byStage.set(si, [t.seed])
  }
  return {
    runDir: ctx.runDir,
    out: ctx.out,
    policy: ctx.policy,
    weights: ctx.weights,
    difficulty: ctx.difficulty,
    maxTicks: ctx.maxTicks,
    distCfgPath: ctx.cfgPath,
    localSlots: ctx.localSlots,
    iterId: ctx.iterId,
    fresh,
    // 一次性 CLI：窗口不截断（Python 侧默认 86400s）；单局超时沿用本工具旧公式
    //（maxTicks/20 + 120：36000 tick 的硬关 ≈ 1920s，别拿 900s 默认值砍掉慢节点上的长局）。
    windowSec: 86400,
    taskTimeoutSec: Math.round(ctx.maxTicks / 20 + 120),
    units: [...byStage.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([stageId, seeds]) => ({ stageId, seeds })),
  }
}

/** Python 侧逐局行（`eval_m1_once.to_m1_row` 的输出契约）。 */
export interface M1DistRow {
  stage: number
  seed: number
  node?: string | null
  ok?: boolean
  outcome?: string | null
  win?: boolean
  cleared?: boolean
  ticks?: number
  kills?: number
  lives?: number | null
  baseAlive?: boolean | null
  firstKillTick?: number | null
  enemyTotal?: number | null
  playerDeaths?: number | null
  playerShots?: number | null
  powerUpsCollected?: number | null
  playerLevel?: number | null
  cellsVisited?: number | null
  /** agent 报告的原始 scorable（scoreV7 的完整输入；旧节点缺这一列）。 */
  scorable?: unknown
}

/** 逐局结果 + agent 原始 scorable（本机 worker 池路径没有这一项）。 */
export interface DistResult extends SimTaskResult {
  scorable?: ScorableRun
}

/**
 * 逐局行 → SimTaskResult（纯函数，可单测）。
 *
 * `scorable` 原样带上（打分输入不做字段级搬运 = 不会两端漂移）；旧节点/缺列时按标量列
 * 合成一份 telemetry，让 scoreV7 仍能算（缺的维度按 null 跳过，不是伪造成 0）。
 */
export function rowToSimResult(row: M1DistRow, id: number): DistResult {
  const sc = (row.scorable ?? null) as ScorableRun | null
  const telemetry: RunTelemetry | undefined =
    (sc?.telemetry as RunTelemetry | undefined) ??
    ({
      enemyTotal: row.enemyTotal ?? 0,
      startLives: 0,
      playerDeaths: row.playerDeaths ?? 0,
      playerShots: row.playerShots ?? 0,
      powerUpsSpawned: 0,
      powerUpsCollected: row.powerUpsCollected ?? 0,
      starsCollected: 0,
      finalPlayerLevel: row.playerLevel ?? 0,
      baseWallIntact: 0,
      baseWallTotal: 0,
      basePressureMean: 0,
      basePressureSamples: 0,
      cellsVisited: row.cellsVisited ?? 0,
      deaths: [],
    } as RunTelemetry)
  const outcome = String(row.outcome ?? 'error')
  return {
    id,
    ok: row.ok !== false && outcome !== 'error',
    outcome,
    ticks: row.ticks ?? 0,
    killCount: row.kills ?? 0,
    baseAlive: row.baseAlive ?? sc?.finalState?.baseAlive ?? true,
    cleared: row.cleared === true,
    lives: row.lives ?? sc?.finalState?.lives ?? undefined,
    firstKillTick: row.firstKillTick ?? undefined,
    telemetry,
    scorable: sc ?? undefined,
  }
}

/**
 * 跑一轮分派：写 spec → 经 `nn-py-safe.sh` 调 Python → 逐局行归位。
 * 返回本轮的**来源计数**（`local` / `node:<id>`），供 provenance 注脚。
 */
async function runPythonDist(
  ctx: DistCtx,
  batchTasks: SimTask[],
  progress: (done: number, total: number) => void,
  onSettle: (i: number, res: DistResult) => void,
  fresh: boolean,
): Promise<Record<string, number>> {
  const spec = buildDistSpec(ctx, batchTasks, fresh)
  mkdirSync(ctx.runDir, { recursive: true })
  const specPath = `${ctx.runDir}/spec.json`
  writeFileSync(specPath, JSON.stringify(spec, null, 2))
  process.stderr.write(
    `[m1-eval] dist via python: policy=${ctx.policy} ${batchTasks.length} games, ` +
      `nodes=${ctx.cfgPath}, localSlots=${ctx.localSlots}${fresh ? ' (--fresh)' : ' (续跑)'}\n`,
  )
  // 同步等待（spawnSync）：Python 的日志直接进 stderr，stdout 留给本工具的 JSON 报告。
  const proc = spawnSync('bash', [PY_SAFE, PY_ENTRY, '--spec', specPath], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'inherit', 'inherit'],
  })
  if (proc.error) process.stderr.write(`[m1-eval] python 调用失败: ${proc.error.message}\n`)
  const bySrc: Record<string, number> = {}
  if (!existsSync(spec.out)) {
    process.stderr.write(
      `[m1-eval] python 未产出逐局行（${spec.out}）— exit ${proc.status}；本批按 error 记账\n`,
    )
    return bySrc
  }
  const index = new Map<string, number>()
  for (let i = 0; i < batchTasks.length; i++)
    index.set(`${batchTasks[i].stageIndex ?? 0}:${batchTasks[i].seed}`, i)
  let done = 0
  for (const line of readFileSync(spec.out, 'utf8').split('\n')) {
    if (!line.trim()) continue
    let row: M1DistRow
    try {
      row = JSON.parse(line) as M1DistRow
    } catch {
      continue // 半行/损坏行：跳过（Python 侧写文件是原子的，这里只是防御）
    }
    const i = index.get(`${row.stage}:${row.seed}`)
    if (i === undefined) continue // 续跑台账里的旧行（不属于本轮子集）
    onSettle(i, rowToSimResult(row, batchTasks[i].id))
    done++
    progress(done, batchTasks.length)
    const key = row.node === 'local' ? 'local' : `node:${row.node ?? '?'}`
    bySrc[key] = (bySrc[key] ?? 0) + 1
  }
  return bySrc
}

if (import.meta.main) await main()
