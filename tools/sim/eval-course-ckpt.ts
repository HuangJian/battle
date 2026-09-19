#!/usr/bin/env bun
/**
 * eval-course-ckpt.ts — greedy evaluation of NN-policy checkpoint(s) on a
 * curriculum course's custom stages (2000+), headless.
 *
 * 架构（2026-09-19 重构；用户裁定「不要在 TS 里重新实现一套节点通信和重试」）：
 *   * **本地与分布式都由 Python 引擎跑**：`nn-training/eval_course_once.py` →
 *     `rl.batch_eval.BatchEvalRunner`。节点门 / ping / 退避重试 / 失败连击停用 /
 *     权重下发 / wver(409) / 本机份额 / 队列尾竞速，全部是训练栈里长期实战的
 *     那一份（`dist_common.fetch_task`、`post_weights_parallel`、`rl/queue.py`）。
 *     TS 侧**不再**复制任何节点通信逻辑（旧实现探测/重试/rescan/停用/本机槽位
 *     各写一份，既漂移又漏护栏，已删除）。
 *   * 本文件只做四件事：解析参数 → 写 spec → 经 `nn-py-safe.sh` 调 Python →
 *     读回 JSONL 打汇总。逐局行由 Python 写（含 Phase-0 七列 + `node` 来源列）。
 *   * 本机份额：`--dist-local N` 透传为 `policy.evalLocalSlots` 的**内存**覆盖；
 *     缺省由配置决定（`policy.evalLocalSlots` → `rl.local_slots`）。
 *   * `--policy nn-goal` 是 TS 独有的目标层实验路径（GOAL_SOURCE/GOAL_BIAS 未进
 *     agent 协议），仍走本机 worker 分片，不做分布式。
 *
 * Usage:
 *   bun tools/sim/eval-course-ckpt.ts --course nn-training/levels/ladder-c06.jsonc \
 *       --weights it30=tmp/weights/it30.json --games 800 --seed0 405000 \
 *       --out tmp/x20-settle/backtest-c06.jsonl
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --policy god --games 100
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --weights a.json --dist-local 0
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --no-dist --weights a.json  # 纯本机（Python 本机槽）
 *   bun tools/sim/eval-course-ckpt.ts --course nn-training/levels/ladder-c06.jsonc \
 *       --weights a.json --policy nn-goal --goal-source god   # 本机 worker（无分布式）
 *
 * Output: one JSON row per game (JSONL) to --out; human summary to stderr.
 * 运行目录（spec / Python 账本 eval_log.jsonl / 本机局 workdir）= `<out>.run/`。
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs'
import { dirname, resolve } from 'path'
import { spawnSync } from 'node:child_process'
import { splitRoundRobin, defaultWorkerCount } from '../lib/worker-pool'
import { flag } from '../lib/cli'
import { computeCodeHash } from '../agent/codehash-files'
import { requestNodeUpgrades, resolveUpgradeBranch, upgradeLogLines } from '../lib/node-upgrade'
import { configLocalSlots, pickDistLocal, type LocalSlotsFromConfig } from '../lib/dist-node-gate'
import type { EvalCourseRow, EvalCourseWorkerPayload } from './eval-course-ckpt-worker'

const REPO_ROOT_WORKER = new URL('./eval-course-ckpt-worker.ts', import.meta.url).href
const REPO_ROOT = resolve(import.meta.dir, '..', '..')
const CURRICULA_DIR = 'nn-training/curricula'
const DEFAULT_DIST_CFG = 'nn-training/rl-config.json'
/** 仓根（升级子进程 cwd 与 nn-py-safe.sh 的相对路径解析都用它）。 */
const PY_ENTRY = 'nn-training/eval_course_once.py'
const PY_SAFE = 'tools/githook/nn-py-safe.sh'

interface CourseJson {
  stages: Array<Record<string, unknown> & { name?: string }>
  difficulty?: string
  max_ticks?: number
  player?: { lives?: number; level?: number }
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

/** All --weights values (repeatable). */
function argAll(name: string): string[] {
  const out: string[] = []
  for (let i = 0; i < process.argv.length; i++) {
    if (process.argv[i] === `--${name}` && i + 1 < process.argv.length)
      out.push(process.argv[i + 1])
  }
  return out
}

/**
 * 解析一个 `--weights` 值：支持可选 `label=path` 形式（I4 晋级门 runner 用它对腿
 * 命名），缺省 label = 路径文件名（既有裸路径调用逐字不变）。label 侧不得含路径
 * 分隔符，避免把文件名里带 `=` 的裸路径误拆。
 */
export function parseWeightSpec(spec: string): { path: string; label: string } {
  const eq = spec.indexOf('=')
  if (eq > 0) {
    const label = spec.slice(0, eq)
    const p = spec.slice(eq + 1)
    if (p && !label.includes('/') && !label.includes('\\')) return { path: p, label }
  }
  return { path: spec, label: spec.split(/[\\/]/).pop() ?? spec }
}

/**
 * 去尾逗号（`,]` / `,}`，含跨行）：逐字符扫描，字符串内原样保留（转义感知）。
 * 背景：Python 侧 rl/jsonc.py 容忍尾逗号，课程文件（c6-pickup 起）普遍带尾逗号
 * （oxfmt `trailingComma: all` 还会主动加）；本函数让 TS 侧与 Python 同口径，
 * 否则探针读课程文件直接崩（2026-09-12 实测）。只删 `]`/`}` 前的逗号，中部逗号不动。
 */
export function stripTrailingCommas(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === ',') {
      let j = i + 1
      while (j < text.length && /\s/.test(text[j] ?? '')) j++
      const n = text[j] ?? ''
      if (n === ']' || n === '}') {
        i++
        continue
      }
    }
    out += c
    i++
  }
  return out
}

/** 课程文件完整管线：去注释 → 去尾逗号 → JSON.parse 可直读。 */
export function parseCourseJsonc(text: string): unknown {
  return JSON.parse(stripTrailingCommas(stripJsonc(text)))
}

/** Strip // and block comments from JSONC, respecting strings (course files). */
export function stripJsonc(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    const n = text[i + 1]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === '/' && n === '/') {
      while (i < text.length && text[i] !== '\n') i++
      continue
    }
    if (c === '/' && n === '*') {
      i += 2
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++
      i += 2
      continue
    }
    out += c
    i++
  }
  return out
}

function resolveCourse(nameOrPath: string): string {
  if (nameOrPath.endsWith('.jsonc') || nameOrPath.endsWith('.json')) return nameOrPath
  return `${CURRICULA_DIR}/${nameOrPath}.jsonc`
}

function parseRangeInt(spec: string | undefined, fallback: number): number {
  if (spec === undefined) return fallback
  const n = parseInt(spec, 10)
  return Number.isInteger(n) && n > 0 ? n : fallback
}

/** Python 入口的 spec（写盘交给 `eval_course_once.py --spec`，避免 argv 引号地狱）。 */
export interface CourseOnceSpec {
  course: string
  weights: Array<{ label: string; path: string }>
  games: number
  seed0: number
  policy: 'nn' | 'god'
  iterId: string
  runDir: string
  out: string
  noNodes: boolean
  localSlots?: number
  distCfgPath?: string
  windowSec?: number
}

export function buildSpec(o: {
  course: string
  weights: Array<{ label: string; path: string }>
  games: number
  seed0: number
  policy: 'nn' | 'god'
  iterId: string
  runDir: string
  out: string
  noNodes: boolean
  localSlots?: number
  distCfgPath?: string
  windowSec?: number
}): CourseOnceSpec {
  const spec: CourseOnceSpec = {
    course: o.course,
    weights: o.weights,
    games: o.games,
    seed0: o.seed0,
    policy: o.policy,
    iterId: o.iterId,
    runDir: o.runDir,
    out: o.out,
    noNodes: o.noNodes,
  }
  if (o.localSlots !== undefined) spec.localSlots = o.localSlots
  if (o.distCfgPath) spec.distCfgPath = o.distCfgPath
  if (o.windowSec !== undefined) spec.windowSec = o.windowSec
  return spec
}

interface LabelAgg {
  label: string
  games: number
  wins: number
  cleared: number
  /** 单关训练场景下 `win`(stage_clear) 与 `cleared`(歼灭) 等价，统一算「过关」。
   *  敌人全灭后若场上还有道具，游戏进 BONUS TIME 窗口（≈600 tick）才 stage_clear，
   *  而 max_ticks 可能在窗口结束前截断 ⇒ outcome=max_ticks 但实际已歼灭。
   *  （2026-09-12 用户裁定：单关场景不区分二者；道具跨关累积的增益只在多关训练时才存在。） */
  passed: number
  outcomes: Record<string, number>
  kills: number
  enemyHits: number
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  ticks: number
}

function summarize(
  rows: EvalCourseRow[],
  outPath: string | undefined,
  t0: number,
  /** 本批是否开了分布式（`--no-dist`/无配置 = false）。仅当开了却 0 远端参与才告警——
   *  显式本机跑不是降级，不该发 WARN。 */
  distEnabled = false,
): void {
  const agg = new Map<string, LabelAgg>()
  for (const r of rows) {
    let a = agg.get(r.label)
    if (!a) {
      a = {
        label: r.label,
        games: 0,
        wins: 0,
        cleared: 0,
        passed: 0,
        outcomes: {},
        kills: 0,
        enemyHits: 0,
        playerHits: 0,
        playerDamageTaken: 0,
        playerShots: 0,
        ticks: 0,
      }
      agg.set(r.label, a)
    }
    a.games++
    if (r.win) a.wins++
    if (r.cleared) a.cleared++
    if (r.win || r.cleared) a.passed++
    a.outcomes[r.outcome] = (a.outcomes[r.outcome] ?? 0) + 1
    a.kills += r.kills
    a.enemyHits += r.enemyHits
    a.playerHits += r.playerHits
    a.playerDamageTaken += r.playerDamageTaken
    a.playerShots += r.playerShots
    a.ticks += r.ticks
  }
  const el = ((Date.now() - t0) / 1000).toFixed(1)
  process.stderr.write(
    `\n[eval-course-ckpt] ${rows.length} games in ${el}s (${(rows.length / Number(el) || 0).toFixed(1)} games/s)\n`,
  )
  process.stderr.write(
    `${'label'.padEnd(28)} pass   kills  hit(敌) beHit(玩家) dmg     shots  avgTicks  max_ticks gameover\n`,
  )
  for (const a of agg.values()) {
    process.stderr.write(
      `${a.label.padEnd(28)} ${`${a.passed}/${a.games}`.padEnd(6)} ${String(a.kills).padEnd(6)} ` +
        `${String(a.enemyHits).padEnd(7)} ${String(a.playerHits).padEnd(10)} ` +
        `${String(a.playerDamageTaken).padEnd(7)} ${String(a.playerShots).padEnd(6)} ` +
        `${Math.round(a.ticks / Math.max(1, a.games))
          .toString()
          .padEnd(9)} ` +
        `${String(a.outcomes['max_ticks'] ?? 0).padEnd(9)} ${a.outcomes['gameover'] ?? 0}\n`,
    )
  }
  // 参与度账（provenance）：**谁跑的必须自证**。只打汇总表会让“熔断/满负荷静默降本地”
  // 看起来像分布式跑（2026-09-19 实测：x20-powered it0 探针 200 局全落本地，日志只有
  // 十几行 requeued）。行里的 `node` 列是同一份账的落盘形态，此处按工具口径再算一遍。
  const bySource: Record<string, number> = {}
  for (const r of rows) {
    const k = r.node ?? 'unknown'
    bySource[k] = (bySource[k] ?? 0) + 1
  }
  const remote = Object.entries(bySource)
    .filter(([k]) => k !== 'local')
    .reduce((s, [, v]) => s + v, 0)
  const localCount = bySource['local'] ?? 0
  const srcText =
    Object.entries(bySource)
      .map(([k, v]) => `${k}=${v}`)
      .join(', ') || 'none'
  process.stderr.write(
    `[eval-course-ckpt] provenance: ${srcText}（共 ${rows.length} 局：远端 ${remote} / 本地 ${localCount}）\n`,
  )
  if (distEnabled && remote === 0 && rows.length > 0 && localCount > 0)
    process.stderr.write(
      `[eval-course-ckpt] ⚠ WARN 远端 0 参与 —— 本批全由本机跑（逐局口径不变，但墙钟慢约 10x）：` +
        `先 ping 各节点 / 确认集群未被训练作业占满\n`,
    )
  process.stderr.write(
    // `pass` = win ∪ cleared（单关场景二者等价，见 LabelAgg.passed 注释）；
    // win/cleared 的原始计数仍逐局落在 JSONL 里，需要细分时可离线重算。
    `[eval-course-ckpt] pass rate (= win ∪ cleared) per checkpoint above; ` +
      `full JSONL ${outPath ? `-> ${outPath}` : 'on stdout'}\n`,
  )
}

/** 逐局行读回（Python 已按 id 排序写好；此处只解析 + 打汇总）。 */
function readRows(outPath: string): EvalCourseRow[] {
  const text = readFileSync(outPath, 'utf8').trim()
  if (!text) return []
  return text.split('\n').map((l) => JSON.parse(l) as EvalCourseRow)
}

/**
 * `--upgrade-nodes`：扫描 stale 节点并下发 pull+restart。**探测与判 stale 都不在 TS 里**
 * ——`nn-training/dist_upgrade_cli.py` 直接调训练循环的
 * `dist_common.upgrade_stale_nodes`（ping → codeHash ≠ expected → request_upgrade_guarded），
 * TS 只拼 spec、读结果、打日志（升级要 pull+重启，本步会阻塞至多 ~3s/节点，属显式开关）。
 */
function maybeUpgradeNodes(cfgPath: string): void {
  const up = requestNodeUpgrades({
    repoRoot: REPO_ROOT,
    cfgPath,
    branch: resolveUpgradeBranch(REPO_ROOT),
    expectedHash: computeCodeHash(),
  })
  for (const l of upgradeLogLines('[eval-course-ckpt]', up)) process.stderr.write(`${l}\n`)
}

// ---------------- nn-goal：TS 独有的本机 worker 路径（无分布式） ----------------

function runWorkerOnce(
  w: Worker,
  payload: EvalCourseWorkerPayload,
): Promise<{ results: EvalCourseRow[]; error?: string }> {
  return new Promise((resolve, reject) => {
    const onMsg = (ev: MessageEvent): void => {
      w.removeEventListener('message', onMsg)
      w.removeEventListener('error', onErr)
      const d = ev.data as { results: EvalCourseRow[]; error?: string }
      resolve({ results: d.results ?? [], error: d.error })
    }
    const onErr = (err: unknown): void => {
      w.removeEventListener('message', onMsg)
      w.removeEventListener('error', onErr)
      reject(new Error((err as ErrorEvent)?.message ?? String(err)))
    }
    w.addEventListener('message', onMsg)
    w.addEventListener('error', onErr)
    w.postMessage(payload)
  })
}

/** nn-goal 本机分片：每个 (weight, 轮转片) 一个短命 worker，结果一次性回包。 */
async function runLocalWorkerChunks(
  weights: Array<{ path: string; label: string }>,
  games: number,
  seed0: number,
  stages: Array<{ name: string; json: string }>,
  env: { difficulty: string; maxTicks: number; lives: number; level: number },
  policy: string,
  workers: number,
): Promise<EvalCourseRow[]> {
  const chunks: EvalCourseWorkerPayload[] = []
  for (let wi = 0; wi < weights.length; wi++) {
    const jobs: Array<{ id: number; stageLocal: number; seed: number }> = []
    for (let g = 0; g < games; g++) {
      jobs.push({
        id: wi * games + g,
        stageLocal: g % stages.length,
        seed: seed0 + Math.floor(g / stages.length),
      })
    }
    for (const slice of splitRoundRobin(jobs, workers)) {
      if (slice.length === 0) continue
      chunks.push({
        weightsPath: weights[wi].path,
        label: weights[wi].label,
        policy,
        goalSource: policy === 'nn-goal' ? (process.env.GOAL_SOURCE ?? 'god') : undefined,
        goalBias: policy === 'nn-goal' ? (process.env.GOAL_BIAS ?? undefined) : undefined,
        difficulty: env.difficulty,
        maxTicks: env.maxTicks,
        lives: env.lives,
        level: env.level,
        stages,
        jobs: slice,
      })
    }
  }
  const settled = await Promise.all(
    chunks.map(async (payload) => {
      const w = new Worker(REPO_ROOT_WORKER)
      try {
        return await runWorkerOnce(w, payload)
      } finally {
        w.terminate()
      }
    }),
  )
  const errs = settled.filter((r) => r.error)
  if (errs.length > 0) {
    for (const e of errs) process.stderr.write(`[eval-course-ckpt] ${e.error}\n`)
    process.exit(1)
  }
  return settled.flatMap((r) => r.results).sort((a, b) => a.id - b.id)
}

async function main(): Promise<void> {
  const courseArg = arg('course')
  if (!courseArg) {
    console.error('[eval-course-ckpt] --course <name|path> required')
    process.exit(2)
  }
  const policy = arg('policy') ?? 'nn'
  if (policy !== 'nn' && policy !== 'god' && policy !== 'nn-goal') {
    console.error(`[eval-course-ckpt] unknown --policy '${policy}' (nn|god|nn-goal)`)
    process.exit(2)
  }
  // goal 层测试：nn-goal 的外部目标源（god|heuristic）+ 软偏置强度，透传 env/payload。
  if (policy === 'nn-goal') {
    const src = arg('goal-source') ?? 'god'
    if (src !== 'god' && src !== 'heuristic') {
      console.error(`[eval-course-ckpt] --goal-source must be 'god'|'heuristic'`)
      process.exit(2)
    }
    process.env.GOAL_SOURCE = src
    const bias = arg('goal-bias')
    if (bias !== undefined) {
      const b = Number(bias)
      if (!Number.isFinite(b) || b <= 0) {
        console.error(`[eval-course-ckpt] --goal-bias must be a positive number`)
        process.exit(2)
      }
      process.env.GOAL_BIAS = String(bias)
    }
  }
  const weightPaths = argAll('weights')
  if ((policy === 'nn' || policy === 'nn-goal') && weightPaths.length === 0) {
    console.error(
      `[eval-course-ckpt] --weights <file> required for --policy ${policy} (repeatable)`,
    )
    process.exit(2)
  }
  const weights = policy === 'god' ? [{ path: '', label: 'god' }] : weightPaths.map(parseWeightSpec)
  const games = parseRangeInt(arg('games'), 100)
  const seed0 = parseRangeInt(arg('seed0'), 0)
  const outPath = arg('out')
  const coursePath = resolveCourse(courseArg)
  const course = parseCourseJsonc(readFileSync(coursePath, 'utf8')) as CourseJson
  const stages = course.stages
  if (!Array.isArray(stages) || stages.length === 0) {
    console.error(`[eval-course-ckpt] course has no custom stages: ${courseArg}`)
    process.exit(2)
  }

  // dist 开关：默认「有 nodes 的 rl-config.json 即分布式」（与训练栈同源）；--no-dist 关。
  const noDist = flag('no-dist')
  let distCfgPath = arg('dist-nodes') ?? ''
  if (!distCfgPath && !noDist) {
    try {
      if (
        existsSync(DEFAULT_DIST_CFG) &&
        readFileSync(DEFAULT_DIST_CFG, 'utf8').includes('"nodes"')
      )
        distCfgPath = DEFAULT_DIST_CFG
    } catch {
      /* 纯本机 */
    }
  }
  const distLocalRaw = arg('dist-local')
  const distLocalParsed = distLocalRaw === undefined ? NaN : parseInt(distLocalRaw, 10)
  if (distLocalRaw !== undefined && (!Number.isFinite(distLocalParsed) || distLocalParsed < 0)) {
    console.error('[eval-course-ckpt] --dist-local must be a non-negative integer')
    process.exit(2)
  }
  // 本机份额由共享纯函数解析（**不许自己另立一套**）：
  //   `--dist-local` > `policy.evalLocalSlots` > `rl.local_slots` > 物理核数，
  // 与 `m1-eval` 同源、与 DECISIONS §2026-09-19 追记二同序。旧实现让 Python 侧
  // 缺省 `evalLocalSlots`（=4），**整条配置链被静默跳过** ⇒ `rl.local_slots: 0`
  // （本机不参与）的机器口径被违反（用户 2026-09-19 实测报障，2026-09-19 复现）。
  const distLocalCfg: LocalSlotsFromConfig = distCfgPath
    ? configLocalSlots(distCfgPath)
    : { slots: null, source: '' }
  const { slots: distLocal, source: distLocalSource } = pickDistLocal(
    distLocalParsed,
    distLocalCfg,
    defaultWorkerCount(),
  )
  const iterId = arg('iter-id') ?? `evalcourse-${Date.now()}`
  const windowSec = parseRangeInt(arg('window-sec'), 86400)
  const t0 = Date.now()

  // ---- nn-goal：本机 worker 路径（GOAL_* 未进 agent 协议，无法经 Python 引擎分发）----
  if (policy === 'nn-goal') {
    const stagePayloads = stages.map((s) => ({ name: s.name ?? 'custom', json: JSON.stringify(s) }))
    process.stderr.write(
      `[eval-course-ckpt] course=${coursePath} stages=${stages.length} games/weights=${games} ` +
        `policy=nn-goal weights=${weights.length} local-only iterId=${iterId}\n`,
    )
    const rows = await runLocalWorkerChunks(
      weights,
      games,
      seed0,
      stagePayloads,
      {
        difficulty: course.difficulty ?? 'hard',
        maxTicks: course.max_ticks ?? 36000,
        lives: course.player?.lives ?? 3,
        level: course.player?.level ?? 0,
      },
      policy,
      defaultWorkerCount(),
    )
    if (outPath) {
      mkdirSync(dirname(outPath), { recursive: true })
      writeFileSync(outPath, rows.map((r) => JSON.stringify(r)).join('\n') + '\n')
    } else {
      for (const r of rows) console.log(JSON.stringify(r))
    }
    // nn-goal 强制本机（GOAL_* 未进 agent 协议）⇒ 不涉分派，provenance 不告警
    summarize(rows, outPath, t0, false)
    return
  }

  // ---- nn / god：Python 引擎（BatchEvalRunner：节点门/重试/停用/权重/本机份额一套）----
  if (flag('upgrade-nodes') && distCfgPath) maybeUpgradeNodes(distCfgPath)
  const runDir = `${outPath ?? `tmp/eval-course-${iterId}`}.run`
  const spec = buildSpec({
    course: coursePath,
    weights,
    games,
    seed0,
    policy: policy as 'nn' | 'god',
    iterId,
    runDir,
    out: outPath ?? `${runDir}/rows.jsonl`,
    noNodes: !distCfgPath,
    localSlots: distLocal,
    distCfgPath: distCfgPath || undefined,
    windowSec,
  })
  process.stderr.write(
    `[eval-course-ckpt] 本机槽位 distLocal=${distLocal}（来源：${distLocalSource}）\n`,
  )
  mkdirSync(runDir, { recursive: true })
  const specPath = `${runDir}/spec.json`
  writeFileSync(specPath, JSON.stringify(spec, null, 1))
  process.stderr.write(
    `[eval-course-ckpt] course=${coursePath} stages=${stages.length} games/weights=${games} ` +
      `policy=${policy} weights=${weights.length} difficulty=${course.difficulty ?? 'hard'} ` +
      `max_ticks=${course.max_ticks ?? 36000} lives=${course.player?.lives ?? 3} ` +
      `level=${course.player?.level ?? 0} ` +
      (distCfgPath ? `dist=${distCfgPath}` : 'dist=local') +
      ` distLocal=${distLocal}` +
      ` iterId=${iterId} runDir=${runDir}\n`,
  )

  // 走官方解释器包装（AGENTS §0.1 规则 13：nn python 一律经 nn-py-safe.sh）
  const proc = spawnSync('bash', [PY_SAFE, PY_ENTRY, '--spec', specPath], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'inherit', 'inherit'],
  })
  if (proc.error) {
    console.error(`[eval-course-ckpt] python 调用失败: ${proc.error.message}`)
    process.exit(1)
  }
  if (!existsSync(spec.out)) {
    console.error(`[eval-course-ckpt] 未产出逐局行（${spec.out}）— python exit ${proc.status}`)
    process.exit(1)
  }
  const rows = readRows(spec.out)
  summarize(rows, outPath ?? spec.out, t0, Boolean(spec.distCfgPath))
  const expected = games * (policy === 'god' ? 1 : weights.length)
  if (rows.length !== expected) {
    console.error(
      `[eval-course-ckpt] incomplete rows ${rows.length}/${expected}（节点忙/不可达时属预期）— exit 1`,
    )
    process.exit(1)
  }
}

if (import.meta.main) await main()
