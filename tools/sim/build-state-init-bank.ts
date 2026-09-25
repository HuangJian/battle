#!/usr/bin/env bun
/**
 * build-state-init-bank.ts —— 人类 demo 中段**世界快照**银行（plan/x20-state-init.plan.md P0）。
 *
 * 产物（离线一次性，**不入库**，见 nn-training/.gitignore）：
 *   <out>/manifest.json                     索引：recipe + 逐局 cuts[] + 每切点 sha256
 *   <out>/snapshots/<stage>-<seed>-t<tick>.json    `cloneWorld` 原样（含 rngState/frame/道具/计时器）
 *
 * 语义（为什么是快照而不是「磁带快进」——plan §2 B1/B2/B5/B6）：
 *   状态 = 人类 demo **真正到达过**的世界（restore 后照原样），采集侧以该局抽到的 seed 重开 RNG
 *   再交棒。于是「起始分布」是注入的**状态**，不是用人类输入在另一个世界里碰运气重建的近似；
 *   idle（人类占 20–30% 帧）也不需要在动作空间里表达——`ReplayInput` 原生 `direction: null`。
 *
 * 保真门（外部证据，plan §2 M4）：切点一律取 `hashInterval` 的整数倍 ⇒ 每个存入的快照都能与
 * 录像自带的 tickHashes **逐点对账**；不符 = 该局整局拒收（响亮），绝不静默入库。
 *
 * 切点规则（recipe 住课程，物化在这里；P3 只从 cuts[] 里抽，plan §2「M5 前那条」）：
 *   cuts = { t = cut_from + n*cut_step : t <= ticks + cut_to, t % K == 0, t % hashInterval == 0 }
 *   `cut_to` 负值 = 从局尾回退（排除终局前那段 trivial 态）。
 *
 * 准入：`<replays 目录>/verdicts.jsonl`（或 `--verdicts`）里 band == solvable 且 kills >= --min-kills；
 *   无 verdict 的目录/局**默认排除**（不静默收编）。
 *
 * 用法：
 *   bun tools/sim/build-state-init-bank.ts \
 *     --replays tmp/human-x20-replays/w1 --replays tmp/human-x20-replays/wwave2 \
 *     --replays tmp/human-x20-replays/wwave3 \
 *     --out nn-training/data/state-init-bank
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { ReplayInput } from '../../src/replay/ReplayInput'
import { parseReplayFile } from '../../src/replay/file'
import { worldTickHash } from '../../src/replay/tickHash'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs'
import { createHash } from 'crypto'
import { dirname, join } from 'path'

/** 决策间隔（与 `export-rl-rollout.ts::K` 同值；切点必须落在决策边界上——plan §2 B6）。 */
export const K = 10

export interface CutRecipe {
  cut_from: number
  cut_to: number
  cut_step: number
}

/**
 * 物化切点列表（纯函数，升序）。
 *
 * 对齐要求是有原因的，**不是**风格问题：
 *   · `% K == 0`：交棒点必须是决策边界。否则首个决策前有 K−1 tick 由「上一帧残留动作」驱动
 *     （未 reset 时甚至继续开火）——静默污染，且 metrics 首行的 tick 不是你以为的那个。
 *   · `% hashInterval == 0`：这是保真门的**前提**。录像只在 hashInterval 的整数倍上留 tickHash，
 *     切点不对齐 ⇒ 这个切点没有外部证据可对（门退化成自证）。
 * 两条都不满足 ⇒ 响亮抛错（不钳制到最近的合法值：那会让课程写的 recipe 与实际产物分叉）。
 */
export function materializeCuts(
  ticks: number,
  recipe: CutRecipe,
  k = K,
  hashInterval = 100,
): number[] {
  const { cut_from, cut_to, cut_step } = recipe
  if (!Number.isInteger(ticks) || ticks <= 0) throw new Error(`ticks 非法：${ticks}`)
  if (cut_from < 0) throw new Error(`cut_from 不得为负：${cut_from}`)
  if (cut_step < 1) throw new Error(`cut_step 必须 ≥1：${cut_step}`)
  if (cut_to > 0) throw new Error(`cut_to 必须 ≤0（负值 = 从局尾回退）：${cut_to}`)
  if (cut_from % k !== 0 || cut_step % k !== 0) {
    throw new Error(
      `切点必须落在决策边界上（%${k} == 0）：cut_from=${cut_from} cut_step=${cut_step}`,
    )
  }
  if (cut_from % hashInterval !== 0 || cut_step % hashInterval !== 0) {
    throw new Error(
      `切点必须对齐 tickHash 采样间隔（%${hashInterval} == 0）——否则保真门退化成自证：` +
        `cut_from=${cut_from} cut_step=${cut_step}`,
    )
  }
  const hi = ticks + cut_to // 上界（cut_to 负值 ⇒ 从局尾回退）
  const out: number[] = []
  for (let t = cut_from; t <= hi; t += cut_step) out.push(t)
  return out
}

/** 该 tick 完成时应当已写入的 tickHash 条数（0 = 该 tick 没有 checkpoint）。 */
export function hashIndexFor(tick: number, hashInterval: number): number {
  if (hashInterval <= 0 || tick % hashInterval !== 0) return 0
  return tick / hashInterval
}

export function sha256Bytes(b: Uint8Array | string): string {
  return createHash('sha256').update(b).digest('hex')
}

interface CutRef {
  tick: number
  file: string
  sha256: string
}

interface BankGame {
  id: string
  stage: number
  demoSeed: number
  ticks: number
  kills: number
  /** 本局核过的 tickHash 条数（= 最后一个切点 / hashInterval）。 */
  hashChecks: number
  cuts: CutRef[]
}

export interface BankManifest {
  version: number
  recipe: CutRecipe & { k: number; hash_interval: number; min_kills: number }
  source: string[]
  totals: {
    games: number
    snapshots: number
    skipped: number
    hash_checks: number
    /** 交棒后剩余决策步的均值（首轮 `est_samples_per_game` 的兜底来源）。 */
    mean_remaining_samples: number
  }
  skipped: { file: string; reason: string }[]
  games: BankGame[]
}

function fail(msg: string): never {
  console.error(`[build-state-init-bank] ${msg}`)
  process.exit(2)
}

function collectReplayFiles(inputs: string[]): string[] {
  const out: string[] = []
  for (const p of inputs) {
    const st = statSync(p, { throwIfNoEntry: false })
    if (!st) fail(`replays 路径不存在: ${p}`)
    if (st!.isDirectory()) {
      for (const f of readdirSync(p).sort()) {
        if (f.endsWith('.replay')) out.push(join(p, f))
      }
    } else if (p.endsWith('.replay')) {
      out.push(p)
    } else fail(`非 .replay 文件: ${p}（目录或 .replay 二选一）`)
  }
  return out
}

/** 目录内（或显式 `--verdicts`）的裁决表：`stage/seed` → verdict。 */
export function loadVerdicts(path: string): Map<string, any> {
  const m = new Map<string, any>()
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const l = line.trim()
    if (!l) continue
    const v = JSON.parse(l)
    m.set(`${v.stage}/${v.seed}`, v)
  }
  return m
}

export interface ConvertOpts {
  recipe: CutRecipe
  k: number
  minKills: number
  snapshotsDir: string
  /** 相对 manifest 的路径前缀（写进 manifest，读方据此定位）。 */
  filePrefix: string
}

/**
 * 单局：重放到每个切点 → 校验（时钟/存活/tickHash）→ 落盘快照。
 *
 * 一处刻意的顺序：**先核 hash 再 clone**（同一 tick、同一世界对象，两步看的是同一状态）。
 * 失败时把本局已写的快照删干净再返回（半份局比没有更危险——`cuts[]` 与盘上文件必须一一对应）。
 */
export function convertOne(
  file: string,
  verdicts: Map<string, any> | null,
  opts: ConvertOpts,
): { ok: true; game: BankGame; wrote: string[] } | { ok: false; reason: string; wrote: string[] } {
  const wrote: string[] = []
  let parsed: any
  try {
    parsed = parseReplayFile(readFileSync(file, 'utf8'))
  } catch (e) {
    return { ok: false, reason: `parse 抛错：${(e as Error).message}`, wrote }
  }
  if (parsed.error || !parsed.replay) return { ok: false, reason: 'parse 失败', wrote }
  const rp = parsed.replay as any
  const md = rp.metadata ?? {}
  const seed: number = rp.seed
  const total: number = rp.totalTicks ?? 0
  const stage: number = md.stage ?? 0
  if (!total || !Number.isInteger(seed)) return { ok: false, reason: 'totalTicks/seed 缺失', wrote }
  if (!verdicts) return { ok: false, reason: '无 verdicts 表（本局不予收编）', wrote }
  const v = verdicts.get(`${stage}/${seed}`)
  const metadataWins = (md.killCount ?? 0) >= opts.minKills
  if (v && (v.best?.band !== 'solvable' || (v.kills ?? 0) < opts.minKills)) {
    return { ok: false, reason: `verdict 未达标（band/kills，${JSON.stringify(v)}）`, wrote }
  }
  if (!v && !metadataWins) {
    return { ok: false, reason: '无裁决且元数据 killCount 未达标', wrote }
  }
  // 无裁决但元数据 20 杀 ⇒ 收编（与 `export-replay-labels` 的逐局口径同构：那个工具在无 verdicts
  // 文件时同样认元数据；差异只在「整个目录无裁决表」= 拒，见 main）。收编必须**响亮**：
  // 「静默收了一局没有独立裁决的录像」正是这门课将来对不上账时最难查的那种事。
  if (!v) console.log(`[bank] NOTE ${file}: 无裁决，按元数据 killCount=${md.killCount} 收编`)
  const interval: number = rp.hashInterval ?? 100
  const recorded: string[] = rp.tickHashes ?? []
  const cuts = materializeCuts(total, opts.recipe, opts.k, interval)
  if (cuts.length === 0) return { ok: false, reason: `切点为空集（ticks=${total}）`, wrote }
  const lastCut = cuts[cuts.length - 1]
  if (recorded.length * interval < lastCut) {
    return {
      ok: false,
      reason: `tickHashes 覆盖不到切点（${recorded.length}×${interval} < ${lastCut}）`,
      wrote,
    }
  }

  // 布关 = diverge-resim / export-replay-labels 的已验证路径：预置难度/规则/关卡 → restore → 输入替换。
  const world = new World()
  world.rng.reseed(seed)
  const dkey = md.difficulty || 'classic'
  world.difficultyKey = dkey
  world.difficulty = (DIFFICULTIES as any)[dkey] ?? (DIFFICULTIES as any)['classic']
  world.rules = (RULES as any)[dkey] ?? DEFAULT_RULES
  world.loadStageData((STAGES as any[])[stage] ?? (STAGES as any[])[0], 0)
  restoreWorld(world, rp.initialSnapshot)
  const input = new ReplayInput(rp.frames)
  const sim = new Simulation(world, input as any)
  sim.input = input as any
  sim.input2 = (input as any).input2 ?? null
  world.state = 'playing'

  const id = `s${stage}-${seed}`
  const pending = new Set(cuts)
  const cutRefs: CutRef[] = []
  // 人类行为侧面（分析用："交棒后行为是否向人类靠拢"的对照物；不进玩法）。事件读取对仿真
  // 无副作用（tickHash 不含事件；浏览器 loop 本来就逐帧 consumeEvents）。
  const human = { shots: 0, enemyHits: 0, damageTaken: 0, powerUps: 0, stars: 0 }
  let tick = 0
  let hashIdx = 0
  while (tick < total) {
    sim.tick()
    ;(input as any).advance()
    tick++
    for (const e of world.consumeEvents() as any[]) {
      if (e.type === 'player_damage') human.damageTaken += e.damage ?? 0
      else if (e.type === 'bullet_fired' && e.bullet?.isPlayer) human.shots++
      else if (e.type === 'enemy_hit') human.enemyHits++
      else if (e.type === 'powerup_collected') {
        human.powerUps++
        if (e.powerUp === 'star') human.stars++
      }
    }
    if (hashIdx < recorded.length && tick % interval === 0) {
      if (worldTickHash(world) !== recorded[hashIdx]) {
        for (const p of wrote) rmSync(p, { force: true })
        return { ok: false, reason: `tickHash 失配 @tick ${tick}`, wrote: [] }
      }
      hashIdx++
    }
    if (!pending.has(tick)) continue
    pending.delete(tick)
    if (!(world.player?.alive ?? false)) {
      for (const p of wrote) rmSync(p, { force: true })
      return { ok: false, reason: `切点 ${tick} 上玩家不存活（银行只收活状态）`, wrote: [] }
    }
    if (world.frame !== tick) {
      for (const p of wrote) rmSync(p, { force: true })
      return {
        ok: false,
        reason: `世界时钟与重放 tick 不一致（${world.frame} != ${tick}）`,
        wrote: [],
      }
    }
    const snap = cloneWorld(world)
    const expectedHash = recorded[hashIndexFor(tick, interval) - 1]
    const file = `${id}-t${tick}.json`
    const body = JSON.stringify({
      version: 1,
      id,
      stage,
      demoSeed: seed,
      tick,
      tickHash: expectedHash,
      human: { ...human, kills: world.killCount },
      snapshot: snap,
    })
    if (worldTickHash(world) !== expectedHash) {
      for (const p of wrote) rmSync(p, { force: true })
      return { ok: false, reason: `快照前 hash 自检失败 @${tick}`, wrote: [] }
    }
    // 读方往返自证（§2 M4）：用**与采集器同一条路**（`restoreWorld` → `worldTickHash`）验一遍。
    // cloneWorld/restoreWorld 漏字段、快照里多/少一个字段、tickHash 写错——全在这里当场暴露，
    // 而不是等采集侧每局跑起来才发现（那时已经烧了整轮）。
    {
      const scratch = new World()
      restoreWorld(scratch, snap)
      const got = worldTickHash(scratch)
      if (got !== expectedHash) {
        for (const p of wrote) rmSync(p, { force: true })
        return {
          ok: false,
          reason: `快照往返自证失败 @${tick}（${got} != ${expectedHash}）`,
          wrote: [],
        }
      }
    }
    const dest = join(opts.snapshotsDir, file)
    writeFileSync(dest, body)
    wrote.push(dest)
    const sha = sha256Bytes(body)
    cutRefs.push({ tick, file: `${opts.filePrefix}/${file}`, sha256: sha })
    if (cutRefs.length === cuts.length) break
  }
  if (cutRefs.length !== cuts.length) {
    for (const p of wrote) rmSync(p, { force: true })
    return { ok: false, reason: `切点未全部命中（${cutRefs.length}/${cuts.length}）`, wrote: [] }
  }
  return {
    ok: true,
    game: {
      id,
      stage,
      demoSeed: seed,
      ticks: total,
      kills: md.killCount ?? 0,
      hashChecks: lastCut / interval,
      cuts: cutRefs,
    },
    wrote,
  }
}

function main(argv: string[] = process.argv.slice(2)): void {
  const replays: string[] = []
  let outDir = 'nn-training/data/state-init-bank'
  let verdictsPath = ''
  let cutFrom = 300
  let cutTo = -120
  let cutStep = 300
  let minKills = 20
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--replays') replays.push(argv[++i])
    else if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--verdicts') verdictsPath = argv[++i]
    else if (argv[i] === '--cut-from') cutFrom = parseInt(argv[++i], 10)
    else if (argv[i] === '--cut-to') cutTo = parseInt(argv[++i], 10)
    else if (argv[i] === '--cut-step') cutStep = parseInt(argv[++i], 10)
    else if (argv[i] === '--min-kills') minKills = parseInt(argv[++i], 10)
    else fail(`未知参数 ${argv[i]}（--help 无此工具；见文件头用法）`)
  }
  if (replays.length === 0) fail('至少给一个 --replays <file|dir>')
  const recipe: CutRecipe = { cut_from: cutFrom, cut_to: cutTo, cut_step: cutStep }
  const files = collectReplayFiles(replays)
  if (files.length === 0) fail('没找到任何 .replay')
  // recipe 自检（在跑任何一局之前就响亮——坏了的口径不该先烧 61 局）
  materializeCuts(1_000_000, recipe)
  const snapshotsDir = join(outDir, 'snapshots')
  mkdirSync(snapshotsDir, { recursive: true })
  const explicitVerdicts = verdictsPath ? loadVerdicts(verdictsPath) : null
  const verdictsCache = new Map<string, Map<string, any> | null>()
  const games: BankGame[] = []
  const seenIds = new Map<string, string>()
  const skipped: { file: string; reason: string }[] = []
  let snapshots = 0
  let hashChecks = 0

  for (const file of files) {
    const dir = dirname(file)
    let verdicts = explicitVerdicts
    if (!verdicts) {
      const key = dir
      if (!verdictsCache.has(key)) {
        const p = join(dir, 'verdicts.jsonl')
        verdictsCache.set(key, existsSync(p) ? loadVerdicts(p) : null)
      }
      verdicts = verdictsCache.get(key) ?? null
      if (!verdicts) {
        skipped.push({ file, reason: `${dir} 无 verdicts.jsonl（无裁决局不予收编）` })
        continue
      }
    }
    const r = convertOne(file, verdicts, {
      recipe,
      k: K,
      minKills,
      snapshotsDir,
      filePrefix: 'snapshots',
    })
    if (!r.ok) {
      skipped.push({ file, reason: r.reason })
      console.log(`[bank] SKIP ${file}: ${r.reason}`)
      continue
    }
    // 同一局重复录像（重试/多次导出）⇒ 只收第一份（响亮记一行）：两个 id 相同的条目会让
    // P3 的派生抽到两份"同名不同内容"的快照，出问题时无法归因。
    const first = seenIds.get(r.game.id)
    if (first) {
      skipped.push({ file, reason: `重复录像（id=${r.game.id} 已由 ${first} 收编）` })
      console.log(`[bank] SKIP ${file}: 重复录像 id=${r.game.id}（已有 ${first}）`)
      for (const p of r.wrote) rmSync(p, { force: true })
      continue
    }
    seenIds.set(r.game.id, file)
    games.push(r.game)
    snapshots += r.game.cuts.length
    hashChecks += r.game.hashChecks
    console.log(
      `[bank] ok ${r.game.id} ticks=${r.game.ticks} kills=${r.game.kills} cuts=${r.game.cuts.length}`,
    )
  }

  games.sort((a, b) => a.stage - b.stage || a.demoSeed - b.demoSeed)
  skipped.sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : 0))
  const remaining = games.flatMap((g) => g.cuts.map((c) => (g.ticks - c.tick) / K))
  const meanRemaining = remaining.length
    ? Math.round(remaining.reduce((a, b) => a + b, 0) / remaining.length)
    : 0
  const manifest: BankManifest = {
    version: 1,
    recipe: { ...recipe, k: K, hash_interval: 100, min_kills: minKills },
    source: [...replays].sort(),
    totals: {
      games: games.length,
      snapshots,
      skipped: skipped.length,
      hash_checks: hashChecks,
      mean_remaining_samples: meanRemaining,
    },
    skipped,
    games,
  }
  const manifestPath = join(outDir, 'manifest.json')
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2))
  console.log(
    `[bank] DONE games=${manifest.totals.games} snapshots=${snapshots} hashChecks=${hashChecks} ` +
      `skipped=${skipped.length} meanRemainingSamples=${meanRemaining}\n[bank] manifest: ${manifestPath}`,
  )
  if (games.length === 0) fail('一局都没收成（银行是空的）')
}

// 只在被当作脚本直接执行时跑 main（被 import 时只暴露纯函数给用例）。
if (import.meta.main) main()
