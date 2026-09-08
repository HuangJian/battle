/**
 * perf-cmp-rollout.ts —— rollout 引擎/内核 一键性能与字节一致性对比（bun 版）
 *
 * 场景（在其它机器上跑：macOS / Android termux proot / Linux / Windows）：
 *   1. 优化前 vs 优化后（wasm 内核），rollout 产物是否逐字节一致
 *   2. bun(JSC) vs node(V8) 分别执行 rollout，产物是否逐字节一致
 *   3. 优化前：bun / node 各自性能
 *   4. 优化后：bun / node 各自性能
 *
 * 口径：
 *   - 权重：自动取 nn-training/weights 目录树（递归）里最新（mtime）的一个 .json；找不到即报错。
 *   - 字节一致性：只跑 1 关 1 个 seed（stage 0 / seed 1）——确定性产物，单局即可判一致性。
 *   - 性能：跑完整网格（缺省 stage 0-34 经典 35 关 × seed 1-2 × maxTicks 1200；贴近生产 per-tick 2400 的量级又够快，~1 分钟级/组合）；
 *     --stage N / --seed N / --ticks N 可覆盖。网格按 --parallel 分片、多进程并行跑
 *     （exporter 单进程内 stage×seed 是串行的——仓库注释里的既定事实，并行由本脚本做）。
 *   - --reps N：整个（并行）网格重复跑 N 次做性能统计（min/median），压机器噪声；默认 1。
 *   - --parallel P：每组合并行子进程数；默认=机器可用核（os.availableParallelism）。
 *
 * 依赖：bun（打包 exporter；脚本本身用 bun 跑）、node ≥22（node 组；缺失自动跳过）。
 * 不改任何仓库文件：产物全在 tmp/perf-cmp.<pid>/，内核通过「打包产物同级 wasm/ 目录」
 * 切换（conv-wasm 相对产物解析 wasm，见 agent-setup.md §2.1 的坑）。
 * 旧内核来源：--old-wasm / $OLD_WASM / git 祖先 blob（提交前=HEAD；提交后=HEAD~1），
 *   取与工作区 sha 不同的最近一个。
 * 退出码：0=全 PASS；1=有字节不一致或执行失败；2=用法/前置错误。
 */
import { spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  copyFileSync,
  writeFileSync,
  statSync,
} from 'node:fs'
import { fileURLToPath } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
process.chdir(ROOT) // 所有相对路径以仓库根为准

const say = (m: string): void => console.log(`[perf-cmp] ${m}`)
const die = (m: string, code = 2): never => {
  console.error(`[perf-cmp] ❌ ${m}`)
  process.exit(code)
}

// ---------------- CLI ----------------
let OLD_WASM = process.env.OLD_WASM ?? ''
let NODE_BIN = process.env.NODE_BIN ?? ''
let STAGE_SPEC = '0-34' // 缺省：经典 35 关全跑（性能网格）
let SEED_SPEC = '1-2' // 缺省：每关 2 个 seed
let TICKS = 1200
let REPS = 1
/** 物理核探测（win=CIM / darwin=sysctl / linux+android proot=/proc/cpuinfo 的 physical×core 唯一对；失败回落逻辑核）。 */
function detectPhysicalCores(): number {
  const logical =
    (os as unknown as { availableParallelism?: () => number }).availableParallelism?.() ??
    os.cpus().length
  const num = (out: string | undefined): number => {
    const n = parseInt((out ?? '').trim().split(/\s+/)[0] ?? '', 10)
    return Number.isFinite(n) && n > 0 ? n : 0
  }
  try {
    if (process.platform === 'darwin') {
      const n = num(spawnSync('sysctl', ['-n', 'hw.physicalcpu'], { encoding: 'utf8' }).stdout)
      if (n) return n
    } else if (process.platform === 'linux') {
      const txt = readFileSync('/proc/cpuinfo', 'utf8')
      const pairs = new Set<string>()
      let curPhys = ''
      let curCore = ''
      let sawIds = false
      for (const line of txt.split(/\r?\n/)) {
        const m = /^([a-z_ ]+)\s*:\s*(.+)$/.exec(line.trim())
        if (!m) continue
        const key = m[1]!.trim()
        const val = m[2]!.trim()
        if (key === 'processor') {
          if (sawIds) pairs.add(`${curPhys}/${curCore}`)
          curPhys = ''
          curCore = ''
          sawIds = false
        } else if (key === 'physical id') {
          curPhys = val
          sawIds = true
        } else if (key === 'core id') {
          curCore = val
        }
      }
      if (sawIds) pairs.add(`${curPhys}/${curCore}`)
      if (pairs.size > 0) return pairs.size
    } else if (process.platform === 'win32') {
      const r = spawnSync(
        'powershell',
        ['-NoProfile', '-Command', '(Get-CimInstance Win32_Processor).NumberOfCores'],
        { encoding: 'utf8', windowsHide: true, timeout: 15_000 },
      )
      const n = num(r.stdout)
      if (n) return n
    }
  } catch {
    /* 回落逻辑核 */
  }
  return logical
}
const PHYS_CORES = detectPhysicalCores()
let PARALLEL = PHYS_CORES
let KEEP = false
let NO_OLD = false
{
  const a = process.argv.slice(2)
  const take = (i: number): string => a[i + 1] ?? ''
  for (let i = 0; i < a.length; i++) {
    const k = a[i]!
    if (k === '--stage') STAGE_SPEC = take(i++)
    else if (k === '--seed') SEED_SPEC = take(i++)
    else if (k === '--ticks') TICKS = parseInt(take(i++), 10)
    else if (k === '--reps') REPS = parseInt(take(i++), 10)
    else if (k === '--parallel') PARALLEL = parseInt(take(i++), 10)
    else if (k === '--old-wasm') OLD_WASM = take(i++)
    else if (k === '--node-bin') NODE_BIN = take(i++)
    else if (k === '--keep') KEEP = true
    else if (k === '--no-old') NO_OLD = true
    else if (k === '-h' || k === '--help') {
      console.log(`用法：bun tools/sim/perf-cmp-rollout.ts [--stage N] [--seed N] [--ticks 1200] [--reps 1] [--parallel N]
      [--old-wasm <path>] [--node-bin <node>] [--keep] [--no-old]
缺省：权重=nn-training/weights 目录树最新 .json；字节一致性跑 stage0/seed1 各一局；
      性能跑 stage 0-34 × seed 1-2 × maxTicks 1200，按 --parallel（缺省物理核数）并行分片。
--reps N：同一并行网格重复 N 次取性能 min/median（默认 1）。`)
      process.exit(0)
    } else die(`未知参数 ${k}（--help 看用法）`)
  }
}
if (!Number.isFinite(TICKS) || TICKS < 300) die('--ticks 至少 300（太短测不准）')
if (!Number.isFinite(REPS) || REPS < 1) die('--reps ≥ 1')
if (!Number.isFinite(PARALLEL) || PARALLEL < 1) die('--parallel ≥ 1')

// ---------------- 依赖 ----------------
const BUN = process.execPath // 本脚本由 bun 执行 → 打包与跑组都用它
const shaOf = (p: string): string =>
  createHash('sha256').update(readFileSync(p)).digest('hex').slice(0, 16)

function enginePairs(): Array<[string, string]> {
  const pairs: Array<[string, string]> = [['bun', BUN]]
  if (NODE_OK) pairs.push(['node', NODE_BIN!])
  return pairs
}

// node ≥22 探测
let NODE_OK = false
if (!NODE_BIN) {
  const r = spawnSync('node', ['--version'], { encoding: 'utf8' })
  if (r.status === 0 && r.stdout) NODE_BIN = 'node'
}
if (NODE_BIN) {
  const v = spawnSync(NODE_BIN, ['--version'], { encoding: 'utf8' })
  const m = /^v(\d+)\./.exec(v.stdout ?? '')
  if (v.status === 0 && m && Number(m[1]) >= 22) NODE_OK = true
}
say(`bun : ${spawnSync(BUN, ['--version'], { encoding: 'utf8' }).stdout?.trim() ?? '?'}`)
if (NODE_OK)
  say(
    `node: ${spawnSync(NODE_BIN!, ['--version'], { encoding: 'utf8' }).stdout?.trim()}（≥22，参与对比）`,
  )
else say('⚠ node 缺失或 <22 → node 组跳过（可用 --node-bin 指定）')
say(`并行分片: ${PARALLEL}（物理核探测=${PHYS_CORES}）`)

// ---------------- 权重：nn-training/weights 目录树最新 ----------------
function newestWeights(base: string): string | null {
  let bestP: string | null = null
  let bestM = -1
  const walk = (dir: string): void => {
    for (const f of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, f.name)
      if (f.isDirectory()) {
        if (!f.name.startsWith('.')) walk(p)
      } else if (f.name.endsWith('.json')) {
        const m = statSync(p).mtimeMs
        if (m > bestM) {
          bestP = p
          bestM = m
        }
      }
    }
  }
  walk(base)
  return bestP
}
const WEIGHTS =
  newestWeights('nn-training/weights') ??
  die('nn-training/weights 目录树下没有 .json 权重——请先放一个 student 权重（h64/d8/board26）')
say(`权重(自动最新): ${WEIGHTS} (${readFileSync(WEIGHTS).length}B sha256:${shaOf(WEIGHTS)})`)

// ---------------- 工作区 + 打包 ----------------
const WORK = path.join(ROOT, 'tmp', `perf-cmp.${process.pid}`)
const WASMDIR = path.join(WORK, 'wasm')
const OUTROOT = path.join(WORK, 'out')
mkdirSync(WASMDIR, { recursive: true })
mkdirSync(OUTROOT, { recursive: true })
process.on('exit', () => {
  if (!KEEP) rmSync(WORK, { recursive: true, force: true })
})

const KERNEL_NEW = path.join(ROOT, 'src', 'nn', 'wasm', 'conv_feats.wasm')
if (!existsSync(KERNEL_NEW)) die(`缺少 ${KERNEL_NEW}`)
const KERNEL_NEW_SHA = shaOf(KERNEL_NEW)
const BUNDLE = path.join(WORK, 'export-rl-rollout.mjs')
say('打包 exporter（同一 bundle 供两种引擎跑，内核经 wasm/ 目录运行时切换）…')
{
  const r = spawnSync(
    BUN,
    ['build', 'tools/sim/export-rl-rollout.ts', '--target=node', `--outfile=${BUNDLE}`],
    { encoding: 'utf8' },
  )
  if (r.status !== 0) die(`打包失败：${(r.stderr ?? '').slice(-400)}`)
}

// ---------------- 旧内核 ----------------
let OLD_AVAIL = false
const OLD_PATH = path.join(WORK, 'old.wasm')
if (!NO_OLD) {
  if (OLD_WASM && existsSync(OLD_WASM)) {
    copyFileSync(OLD_WASM, OLD_PATH)
    OLD_AVAIL = true
  } else {
    for (const ref of ['HEAD~1', 'HEAD']) {
      const g = spawnSync('git', ['show', `${ref}:src/nn/wasm/conv_feats.wasm`], {
        encoding: 'buffer',
      })
      if (g.status !== 0 || !g.stdout?.length) continue
      writeFileSync(OLD_PATH, g.stdout)
      if (shaOf(OLD_PATH) !== KERNEL_NEW_SHA) {
        OLD_AVAIL = true
        say(`旧内核取自 git ${ref}（sha256:${shaOf(OLD_PATH)}）`)
        break
      }
    }
  }
  if (!OLD_AVAIL) say('⚠ 未找到旧内核（--old-wasm / git 祖先与工作区相同？）→ 旧内核对比项跳过')
}
if (OLD_AVAIL) say(`新旧内核 sha256: new=${KERNEL_NEW_SHA} old=${shaOf(OLD_PATH)}`)

// ---------------- 工具函数 ----------------
/** 展开 stage/seed 规格："0-2,5" → [0,1,2,5] */
function expandSpec(spec: string): number[] {
  const out: number[] = []
  for (const part of spec.split(',')) {
    const p = part.trim()
    if (!p) continue
    const m = /^(\d+)-(\d+)$/.exec(p)
    if (m) {
      const a = Number(m[1])
      const b = Number(m[2])
      if (a > b) die(`无效区间 ${p}`)
      for (let i = a; i <= b; i++) out.push(i)
    } else if (/^\d+$/.test(p)) out.push(Number(p))
    else die(`无法解析 ${p}`)
  }
  return out
}

/** 把值列表切成 ≤p 段（连续），转回 exporter 规格串（逗号分隔） */
function chunkSpec(vals: number[], p: number): string[] {
  const k = Math.min(p, vals.length)
  const per = Math.ceil(vals.length / k)
  const out: string[] = []
  for (let i = 0; i < vals.length; i += per) out.push(vals.slice(i, i + per).join(','))
  return out.length ? out : ['0']
}

function hasAnyShard(outDir: string): boolean {
  if (!existsSync(outDir)) return false
  return readdirSync(outDir, { withFileTypes: true }).some(
    (d) => d.isDirectory() && existsSync(path.join(outDir, d.name, 'obs.npy')),
  )
}

/** 指纹：输出目录内全部文件（shard npy + manifest）的相对路径+sha256 → $WORK/<tag>.sha */
function fingerprint(tag: string, outDir: string): void {
  if (!existsSync(outDir)) return
  const lines: string[] = []
  const walk = (dir: string, rel: string): void => {
    for (const f of readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, f.name)
      if (f.isDirectory()) walk(p, `${rel}/${f.name}`)
      else
        lines.push(`${rel}/${f.name} ${createHash('sha256').update(readFileSync(p)).digest('hex')}`)
    }
  }
  for (const d of readdirSync(outDir, { withFileTypes: true }).filter((x) => x.isDirectory())) {
    walk(path.join(outDir, d.name), d.name)
  }
  lines.sort()
  writeFileSync(path.join(WORK, `${tag}.sha`), lines.join('\n') + '\n')
}

/** 单进程跑一局（字节一致性用）：成功返回 true 并落指纹 */
function runOneGame(
  tag: string,
  engine: string,
  kernel: string,
  stage: number,
  seed: number,
): boolean {
  const out = path.join(OUTROOT, `${tag}.byte`)
  rmSync(out, { recursive: true, force: true })
  mkdirSync(out, { recursive: true })
  copyFileSync(kernel, path.join(WASMDIR, 'conv_feats.wasm'))
  const r = spawnSync(
    engine,
    [
      BUNDLE,
      '--weights',
      WEIGHTS,
      '--out',
      out,
      '--stages',
      String(stage),
      '--seeds',
      String(seed),
      '--max-ticks',
      String(TICKS),
      '--difficulty',
      'classic',
      '--node-label',
      'perf-cmp',
      '--wver',
      'cmp',
    ],
    { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 },
  )
  if (r.status !== 0) {
    say(
      `  ${tag}(字节): 执行失败 rc=${r.status}（日志尾: ${`${r.stderr ?? ''}${r.stdout ?? ''}`.slice(-250).replace(/\n/g, ' ')}）`,
    )
    return false
  }
  if (!hasAnyShard(out)) {
    say(`  ${tag}(字节): 无 shard 产物——权重可能不是 h64/d8/board26`)
    return false
  }
  fingerprint(tag, out)
  return true
}

/** 并行跑一组网格：按 --parallel 沿元素更多的轴分片。返回墙钟 ms；失败抛错。 */
async function runGridParallel(
  prefix: string,
  engine: string,
  kernel: string,
  stageSpec: string,
  seedSpec: string,
): Promise<number> {
  const stages = expandSpec(stageSpec)
  const seeds = expandSpec(seedSpec)
  // 分片：选元素多的轴切（另一个轴整给每片）
  const parts: Array<{ stages: string; seeds: string }> =
    stages.length >= seeds.length
      ? chunkSpec(stages, PARALLEL).map((s) => ({ stages: s, seeds: seedSpec }))
      : chunkSpec(seeds, PARALLEL).map((s) => ({ stages: stageSpec, seeds: s }))
  copyFileSync(kernel, path.join(WASMDIR, 'conv_feats.wasm'))
  const t0 = Date.now()
  const jobs = parts.map(async (part, k) => {
    const out = path.join(OUTROOT, `${prefix}.p${k}`)
    rmSync(out, { recursive: true, force: true })
    mkdirSync(out, { recursive: true })
    await new Promise<void>((resolve, reject) => {
      const child = spawn(
        engine,
        [
          BUNDLE,
          '--weights',
          WEIGHTS,
          '--out',
          out,
          '--stages',
          part.stages,
          '--seeds',
          part.seeds,
          '--max-ticks',
          String(TICKS),
          '--difficulty',
          'classic',
          '--node-label',
          'perf-cmp',
          '--wver',
          'cmp',
        ],
        { stdio: ['ignore', 'pipe', 'pipe'] },
      )
      let tail = ''
      child.stdout.on('data', (c: Buffer) => (tail = (tail + c.toString('utf8')).slice(-2000)))
      child.stderr.on('data', (c: Buffer) => (tail = (tail + c.toString('utf8')).slice(-2000)))
      child.on('error', reject)
      child.on('close', (code) => {
        if (code !== 0) reject(new Error(`分片 ${k} rc=${code}: ${tail.slice(-200)}`))
        else if (!hasAnyShard(out)) reject(new Error(`分片 ${k} 无 shard 产物`))
        else resolve()
      })
    })
  })
  await Promise.all(jobs)
  return Date.now() - t0
}

// ================ 字节一致性：1 关 1 seed（确定性产物，单局即可判） ================
say(`\n[字节一致性] 每个 kernel×engine 各跑 1 局（stage 0 / seed 1 / maxTicks${TICKS}）…`)
{
  const kernels: Array<[string, string]> = [['new', KERNEL_NEW]]
  if (OLD_AVAIL) kernels.push(['old', OLD_PATH])
  for (const [kl, kw] of kernels) {
    for (const [el, engine] of enginePairs()) runOneGame(`${kl}-${el}`, engine, kw, 0, 1)
  }
}
let byteOk = true
for (const [a, b] of [
  ['old-bun', 'new-bun'],
  ['old-node', 'new-node'],
  ['new-bun', 'new-node'],
] as const) {
  const fa = path.join(WORK, `${a}.sha`)
  const fb = path.join(WORK, `${b}.sha`)
  if (!existsSync(fa) || !existsSync(fb)) continue // 旧内核不可用等
  const ok = readFileSync(fa, 'utf8') === readFileSync(fb, 'utf8')
  say(`  ${ok ? '✅' : '❌'} ${a} vs ${b}：${ok ? '逐字节一致' : '不一致'}`)
  if (!ok) byteOk = false
}
if (!NODE_OK) say('  （node 组跳过：无 node≥22）')

// ================ 性能：完整网格（并行分片） ================
const perfCombos = new Map<string, { fail: number; ms: number[] }>()
say(
  `\n[性能] 网格 stage ${STAGE_SPEC} × seed ${SEED_SPEC} × maxTicks${TICKS}，并行 ${PARALLEL}，REPS=${REPS}…`,
)
{
  const kernels: Array<[string, string]> = [['new', KERNEL_NEW]]
  if (OLD_AVAIL) kernels.push(['old', OLD_PATH])
  for (const [kl, kw] of kernels) {
    for (const [el, engine] of enginePairs()) {
      const tag = `${kl}-${el}`
      const c: { fail: number; ms: number[] } = { fail: 0, ms: [] }
      perfCombos.set(tag, c)
      say(`  跑 ${kl} 内核 × ${el}（${REPS} 次）…`)
      for (let i = 0; i < REPS; i++) {
        try {
          c.ms.push(await runGridParallel(tag, engine, kw, STAGE_SPEC, SEED_SPEC))
        } catch (e) {
          c.fail = 1
          say(`  ${tag}.r${i}: ${e instanceof Error ? e.message : String(e)}`)
          break
        }
      }
    }
  }
}
const fmtStats = (name: string): string => {
  const c = perfCombos.get(name)
  if (!c || c.fail) return 'FAIL'
  const s = [...c.ms].sort((a, b) => a - b)
  return `${s[0]}/${s[Math.floor((s.length - 1) / 2)]}`
}
console.log(
  '\n[perf-cmp] ========== 性能（整网格并行墙钟 ms，min/median，REPS=' +
    REPS +
    `，stage ${STAGE_SPEC} / seed ${SEED_SPEC} / maxTicks${TICKS}，parallel=${PARALLEL}）==========`,
)
say(`  优化后 kernel：  bun=${fmtStats('new-bun')}ms   node=${fmtStats('new-node')}ms`)
if (OLD_AVAIL) {
  say(`  优化前 kernel：  bun=${fmtStats('old-bun')}ms   node=${fmtStats('old-node')}ms`)
  const num = (k: string, e: string): number => {
    const c = perfCombos.get(`${k}-${e}`)
    if (!c || c.fail || !c.ms.length) return 0
    return [...c.ms].sort((x, y) => x - y)[0]!
  }
  const ob = num('old', 'bun')
  const nb = num('new', 'bun')
  const on = num('old', 'node')
  const nn = num('new', 'node')
  if (ob && nb && on && nn) {
    say(
      `  加速比: new/old(bun)=${(ob / nb).toFixed(2)}×  new/old(node)=${(on / nn).toFixed(2)}×  node/bun(new)=${(nb / nn).toFixed(2)}×`,
    )
  }
}
say('  注：墙钟=整网格并行完成耗时（节点吞吐视角），同批可比。')
say(`  详情目录（--keep 保留）: ${WORK}`)

process.exit(byteOk ? 0 : 1)
