/**
 * rl-hourly-inspect.ts — RL 训练每小时巡检三合一：
 *   1. 读 training_log.jsonl（run_start / iteration / circuit_break）+ 进程存活探测；
 *   2. 增量扫描每轮 it 目录里 worker 子目录的 _rl_report.json（+ shard 的
 *      manifest.json），把各关累计战绩（games/wins/kills）与最近扫描段
 *      （lastPass）写回 tmp/rl-traj/inspection-state.json；
 *   3. 生成可排序 HTML 报告 tmp/rl-traj/inspection-report.html
 *      （样式与点击排序对齐 tools/sim/scorecard-html.ts）：累计战绩总表 +
 *      本段各关表现表（胜率/击杀/耗时/余命/拾取道具）。
 *
 * 用法：bun tools/diag/rl-hourly-inspect.ts [--dry-run] [--up-to N]
 * 口径：kills = manifest.dims.progress.raw（缺失时 round(dimLists.progress×20)，
 * 分母恒为 ENEMIES_PER_STAGE=20 且不触顶，无损）；余命 = dims.lives.raw；
 * 拾取道具 = dims.loot.raw；单局耗时 = dims.clearSpeed.raw（ticks）。
 * kills 自 2026-08-23 起累计（更早迭代已被 --keep-iters 轮转，无法回补）。
 */

import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { execSync } from 'node:child_process'

const ROOT = resolve(import.meta.dir, '..', '..')
const TRAJ_DIR = join(ROOT, 'tmp', 'rl-traj')
const LOG_PATH = join(TRAJ_DIR, 'training_log.jsonl')
const META_PATH = join(TRAJ_DIR, 'dist-agent-meta.jsonl')
const EVAL_LOG_PATH = join(TRAJ_DIR, 'eval_log.jsonl')
const STATE_PATH = join(TRAJ_DIR, 'inspection-state.json')
const REPORT_PATH = join(TRAJ_DIR, 'inspection-report.html')
const ENEMY_TOTAL = 20
const START_LIVES = 3

const NODE_ALIASES: Record<string, string> = {
  'google-cloud-shell': 'gcs',
  'android-97': 'a97',
  'android-98': 'a98',
  macos: 'mac',
}
function normNode(n: string): string {
  const s = String(n ?? '?')
  return NODE_ALIASES[s] ?? s
}

export interface IterEvent {
  iter: number
  time: string
  winRate: number
  outcomes: Record<string, number>
  score_mean: number | null
  entropy: number | null
  kl: number | null
  ticks: number | null
  rollout_sec?: number | null
  ppo_sec?: number | null
  /** 流式三阶段拆分：纯采集窗口（= collector 墙钟 − 窗口内重叠的 PPO）。 */
  pure_collect_sec?: number | null
  /** 轮内累计 KL（流式 = Σ各 wave，即熔断口径）；串行模式该值即全轮均值。旧日志行缺失——此时看 kl。 */
  kl_cum?: number | null
  /** 本轮 KL 是否触发熔断（停止训练并丢弃后续已结算语料）。 */
  halted?: boolean
  /** 熔断后丢弃的已结算局数。 */
  dropped_games?: number | null
  /** 各维度轮均值（jsonl dim_means 原样透传；旧日志行可能缺失）。 */
  dim_means?: Record<string, number | null> | null
}

/** 本轮实际局数 = outcomes 计数之和（随 rotate/补采配置浮动，勿硬编码）。 */
export function gamesOf(e: IterEvent): number {
  return Object.values(e.outcomes ?? {}).reduce((a, b) => a + (Number(b) || 0), 0)
}

/** 局均 ticks；无 rollout 遥测（games=0 或 ticks 缺失）时返回 null——缺数据不是零值信号。 */
export function avgTicksPerGame(e: IterEvent): number | null {
  const games = gamesOf(e)
  if (games <= 0 || typeof e.ticks !== 'number' || e.ticks <= 0) return null
  return e.ticks / games
}

/**
 * 守家维度轮均值（dim_means.baseIntegrity）——R6 观察项：检验「守家优先奖励」
 * 是否真的在教防守的第一信号（旧谱系该值恒 ~0.25 平台）。旧日志行缺失 → null。
 */
export function baseIntegrityOf(e: IterEvent): number | null {
  const v = e.dim_means?.baseIntegrity
  return typeof v === 'number' ? v : null
}

/**
 * KL 展示口径：优先轮内累计 kl_cum（熔断判据看的就是它）。此前表格显示的 kl 在
 * 流式模式下只是「最后一个 wave 的均值」（~0.03-0.04），对轮内累积漂移（常到
 * 0.12-0.15 并触发熔断）全盲——2026-08-25 用户质询「KL 从没超 0.1」暴露此误导。
 */
export function klEff(e: IterEvent): number | null {
  return e.kl_cum ?? e.kl
}

/** 相位耗时展示：<90s 显示秒，否则分钟；缺失/旧日志行显示 —。 */
export function fmtDur(sec: number | null | undefined): string {
  if (typeof sec !== 'number' || !(sec >= 0)) return '—'
  return sec < 90 ? `${Math.round(sec)}s` : `${(sec / 60).toFixed(1)}m`
}

interface RlReport {
  stages: number[]
  seeds: number[]
  outcomes: Record<string, number>
  totalTicks?: number
  scoreList: number[]
  dimLists?: Record<string, number[]>
}

interface DimScore {
  value: number | null
  raw: number
}

type ManifestDims = Record<string, DimScore>

interface WorkerData {
  report: RlReport
  manifest: ManifestDims | null
}

interface StageEntry {
  nameZh: string
  games: number
  wins: number
  kills: number
}

interface Totals {
  iterations: number
  games: number
  wins: number
  kills: number
}

interface PassStageStat {
  games: number
  wins: number
  kills: number
  ticks: number
  livesSum: number
  lootSum: number
  lootGames: number
}

interface LastPass {
  covered: string
  endedAt: string
  stages: Record<string, PassStageStat>
}

interface InspectionState {
  version: number
  purpose: string
  runStartTime: string
  lastScannedIter: number
  scannedIters: number[]
  coverageNote: string
  totals: Totals
  stageStats: Record<string, StageEntry>
  lastPass?: LastPass
}

interface NewWin {
  iter: number
  stage: number
  name: string
  seed: number
  score: number
  kills: number
}

interface AgentRow {
  node: string
  attempts: number
  ok: number
  fail: number
  wins: number
  elapsedSum: number
  elapsedN: number
  lastIt: number
  lastError: string
  lastGames: number
}

interface HtmlRow {
  idx: number
  dispIdx: number
  name: string
  games: number
  wins: number
  winRate: number
  kills: number
  avgKills: number
}

/** eval_log.jsonl 的 eval_summary 行（干净评估轮汇总，run_rl.py dispatch_eval_round 写出）。 */
export interface EvalSummary {
  iter: number
  wver: string
  time: string
  sec: number | null
  games: number
  wins: number
  winRate: number | null
  dropped: number
  rolloutWinRate: number | null
  nodes: Record<string, number>
}

/** 每个迭代取最新一条 eval_summary（同 iter 重跑时后写覆盖先写）。 */
export function readEvalSummaries(): Map<number, EvalSummary> {
  const out = new Map<number, EvalSummary>()
  if (!existsSync(EVAL_LOG_PATH)) return out
  for (const line of readFileSync(EVAL_LOG_PATH, 'utf8').split('\n')) {
    const t = line.trim()
    if (!t) continue
    let r: Record<string, unknown>
    try {
      r = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    if (r.event !== 'eval_summary' || typeof r.iter !== 'number') continue
    const nodes: Record<string, number> = {}
    if (r.nodes && typeof r.nodes === 'object') {
      for (const [k, v] of Object.entries(r.nodes as Record<string, unknown>)) {
        if (typeof v === 'number') {
          const key = normNode(k)
          nodes[key] = (nodes[key] ?? 0) + v
        }
      }
    }
    out.set(r.iter, {
      iter: r.iter,
      wver: String(r.wver ?? ''),
      time: String(r.time ?? ''),
      sec: typeof r.sec === 'number' ? r.sec : null,
      games: typeof r.games === 'number' ? r.games : 0,
      wins: typeof r.wins === 'number' ? r.wins : 0,
      winRate: typeof r.winRate === 'number' ? r.winRate : null,
      dropped: typeof r.dropped === 'number' ? r.dropped : -1,
      rolloutWinRate: typeof r.rolloutWinRate === 'number' ? r.rolloutWinRate : null,
      nodes,
    })
  }
  return out
}

interface PassHtmlRow {
  idx: number
  dispIdx: number
  name: string
  games: number
  wins: number
  winRate: number
  kills: number
  avgKills: number
  avgSeconds: number
  avgLives: number
  loot: number
}

function parseArgs(): { dryRun: boolean; upTo: number | null; passFrom: number | null } {
  const argv = process.argv.slice(2)
  const dryRun = argv.includes('--dry-run')
  let upTo: number | null = null
  const i = argv.indexOf('--up-to')
  if (i >= 0 && argv[i + 1]) upTo = Number(argv[i + 1])
  let passFrom: number | null = null
  const j = argv.indexOf('--pass-from')
  if (j >= 0 && argv[j + 1]) passFrom = Number(argv[j + 1])
  return { dryRun, upTo, passFrom }
}

interface LogEvent {
  event?: string
  time?: string
  iter?: number
}

function parseLog(): {
  runStarts: string[]
  circuitBreaks: string[]
  iters: Map<number, IterEvent>
} {
  const runStarts: string[] = []
  const circuitBreaks: string[] = []
  const iters = new Map<number, IterEvent>()
  for (const line of readFileSync(LOG_PATH, 'utf8').split('\n')) {
    if (!line.trim()) continue
    let ev: LogEvent
    try {
      ev = JSON.parse(line) as LogEvent
    } catch {
      continue
    }
    if (ev.event === 'run_start' && typeof ev.time === 'string') runStarts.push(ev.time)
    else if (ev.event === 'circuit_break') circuitBreaks.push(line)
    else if (ev.event === 'iteration' && typeof ev.iter === 'number')
      iters.set(ev.iter, ev as unknown as IterEvent)
  }
  return { runStarts, circuitBreaks, iters }
}

function pythonProcCount(): number | null {
  try {
    const out = execSync('tasklist /FI "IMAGENAME eq python.exe" /NH', {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    const m = out.match(/python\.exe/gi)
    return m ? m.length : 0
  } catch {
    return null
  }
}

function ensureEntry(state: InspectionState, idx: number): StageEntry {
  const key = String(idx)
  let e = state.stageStats[key]
  if (!e) {
    e = { nameZh: `Stage ${idx + 1}`, games: 0, wins: 0, kills: 0 }
    state.stageStats[key] = e
  }
  if (typeof e.kills !== 'number') e.kills = 0
  return e
}

function readWorkerManifest(workerDir: string): ManifestDims | null {
  for (const d of readdirSync(workerDir, { withFileTypes: true })) {
    if (!d.isDirectory() || !/^rl_s\d+_seed\d+$/.test(d.name)) continue
    const mp = join(workerDir, d.name, 'manifest.json')
    if (!existsSync(mp)) continue
    try {
      const m = JSON.parse(readFileSync(mp, 'utf8')) as { dims?: ManifestDims }
      return m.dims ?? null
    } catch {
      return null
    }
  }
  return null
}

function scanIterDir(n: number): WorkerData[] {
  const dir = join(TRAJ_DIR, `it${n}`)
  if (!existsSync(dir)) return []
  const out: WorkerData[] = []
  // 本机直连局：w<slot>/_rl_report.json
  for (const d of readdirSync(dir, { withFileTypes: true })) {
    if (!d.isDirectory() || !/^w\d+$/.test(d.name)) continue
    const rp = join(dir, d.name, '_rl_report.json')
    if (!existsSync(rp)) continue
    const report = JSON.parse(readFileSync(rp, 'utf8')) as RlReport
    out.push({ report, manifest: readWorkerManifest(join(dir, d.name)) })
  }
  // 远程 agent 局：dist/<node>/rl_s<stage>_seed<seed>/manifest.json —— 字段与本机
  // 导出摘要同构（stages/seeds/scoreList/outcomes/totalTicks/dimLists），可直接按
  // RlReport 消费；dims 走 dimLists 回退（loot 列显示为缺省）。此前只扫 w* 目录，
  // 远程局被整份报告漏计（2026-08-24 发现）。
  const distDir = join(dir, 'dist')
  if (!existsSync(distDir)) return out
  for (const node of readdirSync(distDir, { withFileTypes: true })) {
    if (!node.isDirectory()) continue
    const nodeDir = join(distDir, node.name)
    for (const shard of readdirSync(nodeDir, { withFileTypes: true })) {
      if (!shard.isDirectory() || !/^rl_s\d+_seed\d+$/.test(shard.name)) continue
      const mp = join(nodeDir, shard.name, 'manifest.json')
      if (!existsSync(mp)) continue
      try {
        const raw = JSON.parse(readFileSync(mp, 'utf8')) as Record<string, unknown>
        if (!Array.isArray(raw.stages) || (raw.stages as number[]).length === 0) continue
        out.push({ report: raw as unknown as RlReport, manifest: null })
      } catch {
        continue
      }
    }
  }
  return out
}

interface GameMetrics {
  stage: number
  seed: number
  score: number
  win: boolean
  kills: number
  lives: number
  loot: number
  ticks: number
}

function metricsOf(w: WorkerData): GameMetrics {
  const r = w.report
  const dl = r.dimLists
  const d = w.manifest
  const num = (x: DimScore | undefined): number =>
    x && typeof x.raw === 'number' ? Math.round(x.raw) : -1
  const kills =
    num(d?.progress) >= 0
      ? num(d?.progress)
      : dl?.progress && dl.progress.length > 0
        ? Math.round(dl.progress[0] * ENEMY_TOTAL)
        : -1
  const lives =
    num(d?.lives) >= 0
      ? num(d?.lives)
      : dl?.lives && dl.lives.length > 0
        ? Math.round(dl.lives[0] * START_LIVES)
        : -1
  const loot = num(d?.loot)
  const csRaw = d?.clearSpeed && typeof d.clearSpeed.raw === 'number' ? d.clearSpeed.raw : -1
  const ticks = csRaw > 0 ? Math.round(csRaw) : typeof r.totalTicks === 'number' ? r.totalTicks : -1
  return {
    stage: r.stages[0],
    seed: r.seeds[0],
    score: r.scoreList[0],
    win: (r.outcomes?.stage_clear ?? 0) >= 1,
    kills,
    lives,
    loot,
    ticks,
  }
}

export function healthVerdict(recent: IterEvent[]): string {
  if (recent.length === 0) return '无数据'
  const entVals = recent.map((e) => e.entropy).filter((x): x is number => x !== null)
  const entMin = entVals.length > 0 ? Math.min(...entVals) : null
  // 无 rollout 遥测的行（如断点续跑合并出的 it1：ticks=0/outcomes={}）不参与
  // ticks 规则——缺数据不是"秒投降"信号。
  const tpgValues = recent.map((e) => avgTicksPerGame(e)).filter((x): x is number => x !== null)
  const tpgMin = tpgValues.length > 0 ? Math.min(...tpgValues) : null
  let klStreak = 0
  let klMaxStreak = 0
  for (const e of recent) {
    klStreak = e.kl !== null && e.kl > 0.15 ? klStreak + 1 : 0
    if (klStreak > klMaxStreak) klMaxStreak = klStreak
  }
  if (
    (entMin !== null && entMin <= 0.6) ||
    klMaxStreak >= 3 ||
    (tpgMin !== null && tpgMin < 1000)
  ) {
    return '异常'
  }
  if ((entMin !== null && entMin < 0.8) || klMaxStreak >= 2 || (tpgMin !== null && tpgMin < 2000)) {
    return '观察'
  }
  return '健康'
}

function fmtNow(): string {
  const d = new Date()
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function esc(s: string): string {
  return s.replace(
    /[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!,
  )
}

/**
 * 读调度器落盘的 dist-agent-meta.jsonl，按节点聚合采样元数据。
 * 记录由 run_rl 的 run_rollout_queue 每交付/失败一局写一行（run_rl.py _record_agent_meta）；
 * 干净评估局也同册入账（eval_dispatch.record，mode:"eval"）——健康表全量计入，
 * 但 okByIt（节点贡献列数据源）排除 eval 行：该列已另行合并 ev.nodes，不排则双算。
 * 行字段：node / [mode] / it / stage / seed / ok / win / elapsedSec(成功) | reason(失败) / ts。
 */
function readAgentMeta(): {
  agents: AgentRow[]
  /** 每节点每迭代成功局数（rollout 贡献列的数据源；不含 eval）。 */
  okByIt: Map<string, Map<number, number>>
} {
  if (!existsSync(META_PATH)) return { agents: [], okByIt: new Map() }
  const by = new Map<string, AgentRow>()
  // 每节点在每轮的成功局数：用于"上轮局数"列（以全局最新迭代为基准，未参与=0）
  const okByIt = new Map<string, Map<number, number>>()
  const ensure = (node: string): AgentRow => {
    let a = by.get(node)
    if (!a) {
      a = {
        node,
        attempts: 0,
        ok: 0,
        fail: 0,
        wins: 0,
        elapsedSum: 0,
        elapsedN: 0,
        lastIt: 0,
        lastError: '',
        lastGames: 0,
      }
      by.set(node, a)
    }
    return a
  }
  for (const line of readFileSync(META_PATH, 'utf8').split('\n')) {
    const t = line.trim()
    if (!t) continue
    let r: Record<string, unknown>
    try {
      r = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    const isEval = r.mode === 'eval'
    const node = normNode(String(r.node ?? '?'))
    const a = ensure(node)
    a.attempts++
    if (r.ok) {
      a.ok++
      if (r.win) a.wins++
      if (typeof r.elapsedSec === 'number') {
        a.elapsedSum += r.elapsedSec
        a.elapsedN++
      }
      if (!isEval && typeof r.it === 'number') {
        let m = okByIt.get(node)
        if (!m) {
          m = new Map<number, number>()
          okByIt.set(node, m)
        }
        m.set(r.it, (m.get(r.it) ?? 0) + 1)
      }
    } else {
      a.fail++
      if (typeof r.reason === 'string') a.lastError = r.reason.slice(0, 40)
    }
    if (typeof r.it === 'number') a.lastIt = Math.max(a.lastIt, r.it)
  }
  let globalLastIt = 0
  for (const a of by.values()) globalLastIt = Math.max(globalLastIt, a.lastIt)
  for (const [node, m] of okByIt) {
    const a = by.get(node)
    if (a) a.lastGames = m.get(globalLastIt) ?? 0
  }
  return { agents: [...by.values()].sort((p, q) => q.ok - p.ok), okByIt }
}

interface PassSection {
  covered: string
  endedAt: string
  rows: PassHtmlRow[]
}

function buildHtml(
  rows: HtmlRow[],
  bannerLines: string[],
  recent: IterEvent[],
  scannedLine: string,
  pass: PassSection,
  agent: AgentRow[],
  evalSummaries: Map<number, EvalSummary>,
  rolloutByItNode: Map<number, Map<string, number>>,
): string {
  const dataJson = JSON.stringify(rows)
  const passJson = JSON.stringify(pass.rows)
  const bannerHtml = bannerLines.map((l) => `<div>${l}</div>`).join('\n')

  const recentRows = [...recent]
    .sort((a, b) => b.iter - a.iter)
    .map((e) => {
      const ev = evalSummaries.get(e.iter)
      const evalSec = ev ? fmtDur(ev.sec ?? null) : '—'
      // 节点贡献 = 节点 rollout 局数（meta 账本）+ eval 局数（summary.nodes），并集按总贡献降序
      const roll = rolloutByItNode.get(e.iter)
      const seenNodes = new Set<string>()
      for (const k of Object.keys(ev?.nodes ?? {})) seenNodes.add(normNode(k))
      if (roll) for (const n of roll.keys()) seenNodes.add(normNode(String(n)))
      const parts = [...seenNodes].map((n) => {
        const r = roll?.get(n) ?? 0
        const v = ev?.nodes?.[n] ?? 0
        return { n, r, v, t: r + v }
      })
      parts.sort((a, b) => b.t - a.t)
      const contrib =
        parts.length === 0 ? '—' : parts.map((p) => `${p.n} ${p.r}+${p.v}`).join(' · ')
      const rollWins = e.outcomes.stage_clear ?? 0
      const rollGames = gamesOf(e)
      const rollWrCell =
        rollGames > 0 ? `${(e.winRate * 100).toFixed(1)}% (${rollWins}/${rollGames})` : '—'
      const evalWrCell =
        ev && typeof ev.winRate === 'number'
          ? `${(ev.winRate * 100).toFixed(1)}% (${ev.wins}/${ev.games})`
          : '—'
      const ke = klEff(e)
      const bi = baseIntegrityOf(e)
      const klCell =
        ke === null
          ? '—'
          : ke.toFixed(4) +
            (e.halted
              ? ` <span class="halt" title="KL 熔断：停止训练并丢弃 ${e.dropped_games ?? '?'} 局已结算语料">⛔${e.dropped_games ?? ''}</span>`
              : '')
      return (
        `<tr><td class="txt">it${e.iter}</td><td>${esc(e.time)}</td>` +
        `<td>${rollWrCell}</td><td>${evalWrCell}</td><td>${e.score_mean == null ? '—' : e.score_mean.toFixed(4)}</td>` +
        `<td>${bi == null ? '—' : bi.toFixed(3)}</td>` +
        `<td>${e.entropy == null ? '—' : e.entropy.toFixed(3)}</td><td>${klCell}</td>` +
        `<td>${(() => {
          const t = avgTicksPerGame(e)
          return t === null ? '—' : Math.round(t)
        })()}</td>` +
        `<td>${fmtDur(e.pure_collect_sec)}</td><td>${fmtDur(e.ppo_sec)}</td><td>${evalSec}</td>` +
        `<td class="txt">${esc(contrib)}</td></tr>`
      )
    })
    .join('\n')

  const totalGames = rows.reduce((a, r) => a + r.games, 0)

  const tablesConfig = [
    `  { id: 't', rows: ${dataJson}, def: 'winRate', dir: -1, textKeys: ['name'],
    row: function(r){
      return '<tr>'
        +'<td class="txt">'+r.dispIdx+'</td>'
        +'<td class="txt">'+r.name+'</td>'
        +'<td>'+r.games+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(r.winRate)+'">'+(r.winRate*100).toFixed(1)+'%</td>'
        +'<td>'+r.kills+'</td>'
        +'<td>'+r.avgKills.toFixed(2)+'</td>'
      +'</tr>';
    } },`,
    `  { id: 'p', rows: ${passJson}, def: 'dispIdx', dir: 1, textKeys: ['name'],
    row: function(r){
      return '<tr>'
        +'<td class="txt">'+r.dispIdx+'</td>'
        +'<td class="txt">'+r.name+'</td>'
        +'<td>'+r.games+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(r.winRate)+'">'+(r.winRate*100).toFixed(1)+'%</td>'
        +'<td>'+r.kills+'</td>'
        +'<td>'+r.avgKills.toFixed(2)+'</td>'
        +'<td>'+r.avgSeconds.toFixed(1)+'</td>'
        +'<td>'+r.avgLives.toFixed(2)+'</td>'
        +'<td>'+r.loot+'</td>'
      +'</tr>';
    } }`,
    `  { id: 'a', rows: ${JSON.stringify(agent)}, def: 'ok', dir: -1, textKeys: ['node'],
    row: function(r){
      var rate = r.attempts>0 ? (100*r.ok/r.attempts).toFixed(1)+'%' : '-';
      var avg = r.elapsedN>0 ? (r.elapsedSum/r.elapsedN).toFixed(1) : '-';
      var wr = r.ok>0 ? r.wins/r.ok : 0;
      return '<tr>'
        +'<td class="txt">'+r.node+'</td>'
        +'<td>'+r.ok+'</td>'
        +'<td>'+r.fail+'</td>'
        +'<td>'+rate+'</td>'
        +'<td>'+avg+'</td>'
        +'<td>'+r.lastGames+'</td>'
        +'<td>'+r.wins+'</td>'
        +'<td class="win-cell" style="'+heat(wr)+'\">'+(wr>0?(wr*100).toFixed(1)+'%':'-')+'</td>'
        +'<td>'+(r.lastIt?('it'+r.lastIt):'-')+'</td>'
        +'<td class="txt na">'+r.lastError+'</td>'
      +'</tr>';
    } }`,
  ].join(',\n')

  const passHeading =
    pass.rows.length > 0
      ? `本段各关表现（${esc(pass.covered)}，截至 ${esc(pass.endedAt)}）`
      : '本段各关表现（暂无扫描段数据）'

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RL 训练各关战绩巡检报告</title>
<style>
  :root { --good:#1a7f37; --bad:#cf222e; --line:#d0d7de; --bg:#fff; --th:#f6f8fa; --ink:#1f2328; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin:0; padding:24px; background:#fafbfc; color:var(--ink); }
  h1 { font-size:20px; margin:0 0 4px; }
  h2 { font-size:15px; margin:22px 0 8px; }
  .meta { color:#57606a; font-size:13px; margin-bottom:14px; line-height:1.6; }
  .suite { background:#eef6ff; border:1px solid #c8e1ff; border-radius:8px; padding:10px 14px; font-size:13px; margin-bottom:16px; line-height:1.8; }
  .wrap { overflow:auto; border:1px solid var(--line); border-radius:8px; background:var(--bg); margin-bottom:18px; }
  table { border-collapse:collapse; font-size:13px; width:100%; }
  thead th { position:sticky; top:0; background:var(--th); border-bottom:2px solid var(--line); padding:8px 10px; text-align:right; white-space:nowrap; cursor:pointer; user-select:none; z-index:1; }
  thead th.txt { text-align:left; }
  thead th:hover { background:#eaeef2; }
  thead th .arrow { color:#0969da; font-size:11px; margin-left:3px; }
  tbody td { padding:6px 10px; text-align:right; border-bottom:1px solid #eaecef; white-space:nowrap; }
  tbody td.txt { text-align:left; }
  tbody tr:nth-child(even) { background:#f9fafb; }
  tbody tr:hover { background:#fff8e1; }
  .win-cell { font-weight:600; }
  .halt { color:#cf222e; font-size:11px; font-weight:600; }
  .na { color:#bbb; }
  .th-note { display:block; font-weight:400; font-size:10px; color:#57606a; white-space:normal; line-height:1.3; margin-top:2px; }
  footer { margin-top:14px; color:#8c959f; font-size:12px; line-height:1.6; }
  code { background:#eef1f4; padding:1px 5px; border-radius:4px; }
</style>
</head>
<body>
<h1>RL 训练各关战绩巡检报告</h1>
<div class="meta">生成时间：${fmtNow()} · ${esc(scannedLine)} · 点击表格任意表头排序（再次点击切换升/降序）。</div>
<div class="suite">${bannerHtml}</div>

<h2>采样机健康（节点采样元数据）</h2>
<div class="wrap">
<table id="a">
  <thead><tr>
    <th class="txt" data-key="node">节点</th><th data-key="ok">成功局</th><th data-key="fail">失败局</th>
    <th data-key="rate">采样成功</th><th data-key="avgSec">局均耗时(s)</th><th data-key="lastGames">上轮局数</th><th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th><th data-key="lastIt">最近迭代</th><th class="txt">最近错误</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>

<h2>迭代健康指标</h2>
<div class="wrap" style="max-height:250px;">
<table>
  <thead><tr>
    <th class="txt">迭代</th><th class="txt">完成时刻</th><th>rollout 胜率</th><th>eval 胜率</th><th>score_mean</th><th>baseIntegrity</th><th>entropy</th><th>KL 累计</th><th>局均 ticks</th><th>采集耗时</th><th>PPO 耗时</th><th>eval 耗时</th><th class="txt">节点贡献</th>
  </tr></thead>
  <tbody>
${recentRows}
  </tbody>
</table>
</div>
<div class="meta">KL 累计口径：流式 = Σ各 wave（熔断判据，上限 policy.streamKlCap 默认 0.12）· ⛔N = 该轮熔断丢弃的已结算局数 · 旧日志行无 kl_cum，显示单值 kl（串行模式即全轮均值）。</div>

<h2>${passHeading}</h2>
<div class="wrap">
<table id="p">
  <thead><tr>
    <th class="txt" data-key="dispIdx">关号</th>
    <th class="txt" data-key="name">关卡名</th>
    <th data-key="games">局数</th>
    <th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th>
    <th data-key="kills">击杀</th>
    <th data-key="avgKills">场均击杀</th>
    <th data-key="avgSeconds">场均耗时(s)</th>
    <th data-key="avgLives">场均余命</th>
    <th data-key="loot">拾取道具</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>

<h2>各关累计战绩（games / wins / kills）</h2>
<div class="wrap">
<table id="t">
  <thead><tr>
    <th class="txt" data-key="dispIdx">关号</th>
    <th class="txt" data-key="name">关卡名</th>
    <th data-key="games">局数</th>
    <th data-key="wins">胜局</th>
    <th data-key="winRate">胜率</th>
    <th data-key="kills">击杀</th>
    <th data-key="avgKills">场均击杀</th>
  </tr></thead>
  <tbody></tbody>
</table>
</div>
<footer>
  关号显示 = 0 基索引 + 1（与 STAGES / STAGE_NAMES_ZH 对应）。<br>
  「本段」= 最近一次增量扫描覆盖的迭代段；余命含 tank 道具加成可大于 3；拾取道具为该关本段拾取总数。<br>
  击杀口径：manifest.dims.progress.raw（缺失时 round(dimLists.progress × ${ENEMY_TOTAL})）；自 2026-08-23 起累计，此前轮转删除的迭代无击杀数据。<br>
  数据源：<code>tmp/rl-traj/training_log.jsonl</code> · 干净评估：<code>tmp/rl-traj/eval_log.jsonl</code> · 累计账本：<code>tmp/rl-traj/inspection-state.json</code>（共 ${totalGames} 局入表）· 节点采样元数据：<code>tmp/rl-traj/dist-agent-meta.jsonl</code>（rollout+eval 合并口径；「节点贡献」列仅 rollout）。
</footer>
<script>
function heat(v){
  var t = Math.max(0, Math.min(1, v));
  var hue = Math.round(t*125);
  return 'background:hsl(' + hue + ' 70% 88%); color:#1f2328;';
}
function initTable(cfg){
  var tbody = document.querySelector('#'+cfg.id+' tbody');
  var sortKey = cfg.def, sortDir = cfg.dir;
  function render(){
    var rows = cfg.rows.slice().sort(function(a,b){
      var av=a[sortKey], bv=b[sortKey];
      if(cfg.textKeys.indexOf(sortKey)>=0){ return sortDir*String(av).localeCompare(String(bv),'zh-Hans-CN'); }
      return sortDir*(av-bv);
    });
    tbody.innerHTML = rows.map(cfg.row).join('');
    document.querySelectorAll('#'+cfg.id+' thead th').forEach(function(th){
      var old = th.querySelector('.arrow'); if(old) th.removeChild(old);
      if(th.dataset.key===sortKey){ var s=document.createElement('span'); s.className='arrow'; s.textContent = sortDir<0?'\\u25BC':'\\u25B2'; th.appendChild(s); }
    });
  }
  document.querySelectorAll('#'+cfg.id+' thead th').forEach(function(th){
    th.addEventListener('click', function(){
      var k = th.dataset.key;
      if(k===sortKey){ sortDir = sortDir*-1; } else { sortKey=k; sortDir = (cfg.textKeys.indexOf(k)>=0)?1:-1; }
      render();
    });
  });
  render();
}
var TABLES = [
${tablesConfig}
];
TABLES.forEach(initTable);
</script>
</body>
</html>`
}

function main(): void {
  const { dryRun, upTo, passFrom } = parseArgs()
  const state = existsSync(STATE_PATH)
    ? (JSON.parse(readFileSync(STATE_PATH, 'utf8')) as InspectionState)
    : {
        version: 1,
        purpose: '首次初始化（run_rl 自动巡检前 state 不存在）',
        runStartTime: '',
        lastScannedIter: 0,
        scannedIters: [],
        coverageNote: '',
        totals: { iterations: 0, games: 0, wins: 0, kills: 0 },
        stageStats: {},
      }
  const { runStarts, circuitBreaks, iters } = parseLog()

  const procs = pythonProcCount()
  const procStr =
    procs === null
      ? '未知(tasklist 失败)'
      : procs > 0
        ? `python.exe ×${procs} 存活`
        : '未发现 python 进程 ⚠'
  const lastRunStart = runStarts[runStarts.length - 1] ?? '(无记录)'
  const iterNums = [...iters.keys()].sort((a, b) => a - b)
  const lastIter = iterNums.length > 0 ? iters.get(iterNums[iterNums.length - 1]) : undefined
  const cbStr =
    circuitBreaks.length === 0
      ? '无 circuit_break'
      : `熔断 ${circuitBreaks.length} 次 ⚠ ${circuitBreaks[circuitBreaks.length - 1]}`

  const scanUpTo =
    upTo ?? (iterNums.length > 0 ? iterNums[iterNums.length - 1] : state.lastScannedIter)

  const fromIter = state.lastScannedIter
  const results: Array<{ iter: number; games: number; wins: number; kills: number }> = []
  const newWins: NewWin[] = []
  const firstEver: string[] = []
  const crossCheckBad: string[] = []
  const passStages: Record<string, PassStageStat> = {}
  let totalGames = 0
  let totalWins = 0
  let totalKills = 0
  let missingDirs = 0

  for (let n = fromIter + 1; n <= scanUpTo; n++) {
    const workers = scanIterDir(n)
    if (workers.length === 0) {
      missingDirs++
      console.log(`WARN it${n} 无可扫报告（目录缺失或为空），跳过`)
      continue
    }
    let g = 0
    let w = 0
    let kSum = 0
    for (const wk of workers) {
      const m = metricsOf(wk)
      const win = m.win
      g++
      if (win) w++
      if (m.kills >= 0) kSum += m.kills
      const entry = ensureEntry(state, m.stage)
      const prevWins = entry.wins
      entry.games++
      if (win) {
        entry.wins++
        newWins.push({
          iter: n,
          stage: m.stage,
          name: entry.nameZh,
          seed: m.seed,
          score: m.score,
          kills: Math.max(m.kills, 0),
        })
        if (prevWins === 0) firstEver.push(`s${m.stage + 1} ${entry.nameZh}`)
      }
      if (m.kills >= 0) entry.kills += m.kills
      const pk = String(m.stage)
      let ps = passStages[pk]
      if (!ps) {
        ps = { games: 0, wins: 0, kills: 0, ticks: 0, livesSum: 0, lootSum: 0, lootGames: 0 }
        passStages[pk] = ps
      }
      ps.games++
      if (win) ps.wins++
      if (m.kills >= 0) ps.kills += m.kills
      if (m.ticks > 0) ps.ticks += m.ticks
      if (m.lives >= 0) ps.livesSum += m.lives
      if (m.loot >= 0) {
        ps.lootSum += m.loot
        ps.lootGames++
      }
    }
    totalGames += g
    totalWins += w
    totalKills += kSum
    results.push({ iter: n, games: g, wins: w, kills: kSum })
    if (!state.scannedIters.includes(n)) state.scannedIters.push(n)
    const ev = iters.get(n)
    const logClears = ev ? (ev.outcomes.stage_clear ?? 0) : -1
    if (logClears >= 0 && logClears !== w) crossCheckBad.push(`it${n}: 扫描=${w} 日志=${logClears}`)
  }

  // --pass-from N：对已入账的历史段只读重扫，仅重建本段聚合（不碰累计账本）
  const passIters: number[] = results.map((r) => r.iter)
  if (passFrom !== null && passFrom <= scanUpTo) {
    for (let n = Math.max(passFrom, 1); n <= Math.min(scanUpTo, fromIter); n++) {
      const histWorkers = scanIterDir(n)
      if (histWorkers.length === 0) continue
      for (const wk of histWorkers) {
        const m = metricsOf(wk)
        const pk = String(m.stage)
        let ps = passStages[pk]
        if (!ps) {
          ps = { games: 0, wins: 0, kills: 0, ticks: 0, livesSum: 0, lootSum: 0, lootGames: 0 }
          passStages[pk] = ps
        }
        ps.games++
        if (m.win) ps.wins++
        if (m.kills >= 0) ps.kills += m.kills
        if (m.ticks > 0) ps.ticks += m.ticks
        if (m.lives >= 0) ps.livesSum += m.lives
        if (m.loot >= 0) {
          ps.lootSum += m.loot
          ps.lootGames++
        }
      }
      passIters.push(n)
    }
    passIters.sort((a, b) => a - b)
  }

  if (results.length > 0) {
    state.lastScannedIter = scanUpTo
    state.totals.iterations += results.length
    state.totals.games += totalGames
    state.totals.wins += totalWins
    state.totals.kills += totalKills
    const span =
      results.length === 1
        ? `it${results[0].iter}`
        : `it${results[0].iter}-it${results[results.length - 1].iter}`
    state.coverageNote += ` Last scan ${fmtNow()} covered ${span} (+${totalGames} games / +${totalWins} wins / +${totalKills} kills).`
  }

  if (passIters.length > 0) {
    const span =
      passIters.length === 1
        ? `it${passIters[0]}`
        : `it${passIters[0]}-it${passIters[passIters.length - 1]}`
    state.lastPass = { covered: span, endedAt: fmtNow(), stages: passStages }
  }

  if (!dryRun && (results.length > 0 || passIters.length > 0))
    writeFileSync(STATE_PATH, JSON.stringify(state, null, 2))

  const recentIters = iterNums
    .slice(-5)
    .map((n) => iters.get(n))
    .filter((e): e is IterEvent => e !== undefined)
  const verdict = healthVerdict(recentIters)
  // 健康表展示全量历史（用户指令 2026-08-24）；healthVerdict 的告警规则仍只看近 5 轮。
  const allIters = iterNums.map((n) => iters.get(n)).filter((e): e is IterEvent => e !== undefined)

  const rows: HtmlRow[] = Object.keys(state.stageStats)
    .map(Number)
    .sort((a, b) => a - b)
    .map((idx) => {
      const e = ensureEntry(state, idx)
      return {
        idx,
        dispIdx: idx + 1,
        name: e.nameZh,
        games: e.games,
        wins: e.wins,
        winRate: e.games > 0 ? e.wins / e.games : 0,
        kills: e.kills,
        avgKills: e.games > 0 ? e.kills / e.games : 0,
      }
    })

  const lp = state.lastPass
  const passRows: PassHtmlRow[] = []
  if (lp) {
    for (const key of Object.keys(lp.stages)
      .map(Number)
      .sort((a, b) => a - b)) {
      const s = lp.stages[String(key)]
      const name = state.stageStats[String(key)]?.nameZh ?? `Stage ${key + 1}`
      passRows.push({
        idx: key,
        dispIdx: key + 1,
        name,
        games: s.games,
        wins: s.wins,
        winRate: s.games > 0 ? s.wins / s.games : 0,
        kills: s.kills,
        avgKills: s.games > 0 ? s.kills / s.games : 0,
        avgSeconds: s.games > 0 ? s.ticks / s.games / 60 : 0,
        avgLives: s.games > 0 ? s.livesSum / s.games : 0,
        loot: s.lootSum,
      })
    }
  }

  const wrAll = state.totals.games > 0 ? state.totals.wins / state.totals.games : 0
  const bannerLines = [
    `<b>进程</b>：${esc(procStr)}　·　<b>熔断</b>：${esc(cbStr)}`,
    `<b>最后 run_start</b>：${esc(lastRunStart)}　·　<b>最后迭代</b>：it${lastIter ? lastIter.iter : '?'} @ ${lastIter ? esc(lastIter.time) : '?'}`,
    `<b>健康判定</b>：<b>${verdict}</b>（近 ${recentIters.length} 轮：${(() => {
      const entVals = recentIters.map((e) => e.entropy).filter((x): x is number => x !== null)
      const klVals = recentIters.map((e) => klEff(e)).filter((x): x is number => x !== null)
      const tpgs = recentIters.map((e) => avgTicksPerGame(e)).filter((x): x is number => x !== null)
      return [
        `entropy 最小 ${entVals.length > 0 ? Math.min(...entVals).toFixed(3) : '—'}`,
        `KL累计 最大 ${klVals.length > 0 ? Math.max(...klVals).toFixed(4) : '—'}`,
        `局均 ticks 最小 ${tpgs.length > 0 ? Math.round(Math.min(...tpgs)) : '—'}`,
      ].join(' · ')
    })()}）　·　<b>阈值</b>：entropy &le;0.6 异常 / &lt;0.8 观察 · KL &gt;0.15 连续3轮 异常 / 连续2轮 观察 · 局均 ticks &lt;1000 异常 / &lt;2000 观察`,
    `<b>累计</b>：${state.totals.games} 局 / ${state.totals.wins} 胜（<b>${(wrAll * 100).toFixed(1)}%</b>）/ ${state.totals.kills} 击杀 · 已扫至 it${state.lastScannedIter}`,
  ]

  const meta = readAgentMeta()
  // 反转索引：meta.okByIt 是 节点→(迭代→局数)，健康表按行取某迭代的全部节点贡献，
  // 需要 迭代→(节点→局数)。
  const rolloutByItNode = new Map<number, Map<string, number>>()
  for (const [node, m] of meta.okByIt) {
    const nk = normNode(node)
    for (const [itN, cnt] of m) {
      let row = rolloutByItNode.get(itN)
      if (!row) {
        row = new Map<string, number>()
        rolloutByItNode.set(itN, row)
      }
      row.set(nk, (row.get(nk) ?? 0) + cnt)
    }
  }
  if (!dryRun) {
    const passSection: PassSection = lp
      ? { covered: lp.covered, endedAt: lp.endedAt, rows: passRows }
      : { covered: '', endedAt: '', rows: [] }
    writeFileSync(
      REPORT_PATH,
      buildHtml(
        rows,
        bannerLines,
        allIters,
        `扫描范围 it${fromIter + 1}–it${scanUpTo}`,
        passSection,
        meta.agents,
        readEvalSummaries(),
        rolloutByItNode,
      ),
    )
  }

  console.log('=== RL 小时巡检 ===')
  console.log(`进程: ${procStr} | ${cbStr}`)
  console.log(
    `日志: 最后 run_start=${lastRunStart} | 最后迭代=it${lastIter ? `${lastIter.iter}@${lastIter.time}` : '?'} | 已确认迭代 ${iterNums.length} 轮`,
  )
  if (results.length > 0) {
    console.log(
      `SCAN: ${results.map((r) => `it${r.iter}=${r.games}g/${r.wins}w/${r.kills}k`).join(' ')}`,
    )
    console.log(
      `CROSS-CHECK: ${crossCheckBad.length === 0 ? '全部一致 ✓' : '不一致 ⚠ ' + crossCheckBad.join('; ')}`,
    )
  } else {
    console.log('SCAN: 无新增已确认迭代' + (missingDirs > 0 ? `（${missingDirs} 个目录缺失）` : ''))
  }
  console.log(`健康判定: ${verdict}`)
  console.log(
    `TOTALS: games=${state.totals.games} wins=${state.totals.wins} kills=${state.totals.kills} lastScannedIter=${state.lastScannedIter}`,
  )
  if (state.lastPass)
    console.log(
      `PASS: 本段 ${state.lastPass.covered}（截至 ${state.lastPass.endedAt}）各关表现已写入报告`,
    )
  if (firstEver.length > 0) console.log(`FIRST-EVER WINS: ${firstEver.join(', ')}`)
  if (newWins.length > 0) {
    console.log('--- NEW WINS ---')
    for (const w of newWins)
      console.log(
        `it${w.iter}  s${w.stage + 1} ${w.name}  seed=${w.seed}  score=${w.score.toFixed(3)}  kills=${w.kills}`,
      )
  }
  if (dryRun) console.log('DRY-RUN: 未写回任何文件')
  else console.log(`STATE 写回完成 | HTML 报告: ${REPORT_PATH}`)
}

if (import.meta.main) main()
