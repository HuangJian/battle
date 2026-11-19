#!/usr/bin/env bun
/**
 * arm64-prebuilt-probe.ts —— **一条命令复核入库的 arm64 内核产物**（a95 / Termux 真机，零编译）。
 *
 * ## 为什么要有它（2026-11-16，用户点名「把 tmp 里的临时探针提成正式脚本」）
 *
 * `--check-prebuilt` 只证明「产物是这份源码编的、字节与 manifest 一致」；它证明不了**在 arm64 上
 * 算得对不对**。而 arm64 恰恰是本机验不了的那一档：测试里的跨平台执行证据只有 WSL 的 linux-x64。
 * 于是「arm64 产物算对没有」过去只能靠节点首用 attestation 在真节点上碰运气。这个脚本把它变成一条
 * 可重跑的命令：USB 连一台 arm64 Android（Termux）就能复核，不进 CI、不需要 clang。
 *
 * ## 两条臂（**都是代码库的产物**，设备上零编译）
 *
 *   A 参考臂 = `git show <rev>:src/nn/conv/prebuilt/linux-arm64/conv_native.so`
 *       —— 老版本（`-ffp-contract=off`，`fmla 0`），即「本机 wasm 的等价算术」= 生产 attestation
 *       的对照臂。**不在设备上现编**：现编会引入 clang 版本/链接参数这些额外变量（用户 2026-11-16
 *       明确要求），而入库产物与现役那份同源同编译器，两臂之间只剩 `-ffp-contract` 一个变量。
 *   B 被测臂 = `src/nn/conv/prebuilt/linux-arm64/conv_native.so`（现役，`-ffp-contract=fast`）。
 *
 * ## 判定
 *
 * 与生产**同一口径**：逐张量 `max|A−B| ≤ 1e-5 × max(1, max|A|)`（`ATTEST_TOLERANCE`），
 * 输入是**真实权重**（`buildModelFromText`）+ 3 组 ±1 随机输入（与 `attest()` 同一种子/算法）。
 * 另外报内核稳态耗时比 —— arm64 上融合是 clang 自动收缩（不是显式 `-mfma`），收益只能实测。
 *
 * ## 用法（仓根）
 *
 *   bun tools/diag/arm64-prebuilt-probe.ts                        # 自动挑设备 + 最新权重
 *   bun tools/diag/arm64-prebuilt-probe.ts --serial 6444d390      # 多台设备时点名
 *   bun tools/diag/arm64-prebuilt-probe.ts --weights tmp/.../weights-rollout-<sha>.json
 *   bun tools/diag/arm64-prebuilt-probe.ts --rev HEAD~1 --old-lib <path>   # 换参考臂
 *   bun tools/diag/arm64-prebuilt-probe.ts --keep                 # 保留设备侧工作目录
 *
 * 退出码：0 = ATTEST=PASS；1 = FAIL 或环境不满足（缺 adb / 无设备 / 缺产物 / 设备上没有 python3）。
 * 环境要求：`adb` 在 PATH（`ADB` 可覆盖）· 设备是 arm64 · Termux 已装（自带 python3，见
 * `tools/agent/agent-setup.md` §3）。
 */
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
process.chdir(ROOT)

/** Termux 应用包名 / 设备上的工作目录（`run-as` 只能进自己的 data 目录）。 */
const TERMUX_PKG = 'com.termux'
/** 设备侧工作目录（相对 `$HOME`）。 */
const DEVICE_WORK = 'battle2-arm64-probe'
/** 本机 scratch（gitignored）。 */
const HOST_SCRATCH = 'tmp/diag-arm64'
/** 目标产物（arm64 只有一条，没有变体）。 */
const NEW_LIB = 'src/nn/conv/prebuilt/linux-arm64/conv_native.so'
const OLD_LIB_IN_REPO = 'src/nn/conv/prebuilt/linux-arm64/conv_native.so'
/** 设备侧探针（本仓库跟踪，随脚本一起 push）。 */
const DEVICE_SCRIPT = 'tools/diag/arm64-prebuilt-probe.py'
const BOARD = 26
const SP = BOARD * BOARD
const IN_CH = 19

interface Run {
  status: number | null
  stdout: string
  stderr: string
}

function argv(name: string, def = ''): string {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1]! : def
}
function has(name: string): boolean {
  return process.argv.includes(`--${name}`)
}
function die(msg: string): never {
  console.error(`[arm64-probe] ${msg}`)
  process.exit(1)
}

const ADB = process.env.ADB || 'adb'

function adb(args: string[], timeout = 120_000): Run {
  const r = spawnSync(ADB, args, { encoding: 'utf8', timeout, windowsHide: true })
  return { status: r.status, stdout: String(r.stdout ?? ''), stderr: String(r.stderr ?? '') }
}

/** 单台设备 id：`--serial` 优先，否则必须恰好一台在线。 */
function pickDevice(): string {
  const explicit = argv('serial', process.env.ANDROID_SERIAL ?? '')
  if (explicit) return explicit
  const r = adb(['devices'])
  if (r.status !== 0)
    die(`adb 不可用（${ADB} devices 失败）：${r.stderr.trim() || r.stdout.trim()}`)
  const ids = r.stdout
    .split(/\r?\n/)
    .slice(1)
    .map((l) => l.trim().split(/\s+/))
    .filter((p) => p.length >= 2 && p[1] === 'device')
    .map((p) => p[0]!)
  if (ids.length === 0) die('没有在线设备（adb devices 只列出空表；先 USB 连接并确认 adb 授权）')
  if (ids.length > 1) die(`检测到多台设备（${ids.join(', ')}）⇒ 用 --serial 指定`)
  return ids[0]!
}

/** `git show <rev>:<path>` 到文件（二进制安全：用 buffer 解码）。 */
function gitShow(rev: string, rel: string, out: string): void {
  const r = spawnSync('git', ['show', `${rev}:${rel}`], {
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true,
  })
  if (r.status !== 0) {
    die(
      `git show ${rev}:${rel} 失败：${String(r.stderr ?? '').trim()}\n` +
        `  （参考臂默认取该 rev 的入库产物；也可用 --old-lib <path> 手工指定）`,
    )
  }
  fs.mkdirSync(path.dirname(out), { recursive: true })
  fs.writeFileSync(out, r.stdout as Buffer)
}

/** 权重：`--weights` 优先，否则取 `tmp/**` 里最新的 `weights-*.json`。 */
function pickWeights(): string {
  const explicit = argv('weights')
  if (explicit) {
    if (!fs.existsSync(explicit)) die(`--weights 不存在：${explicit}`)
    return explicit
  }
  const glob = new Bun.Glob('tmp/**/weights-*.json')
  const all: { p: string; m: number }[] = []
  for (const p of glob.scanSync({ cwd: ROOT, onlyFiles: true })) {
    try {
      all.push({ p: path.join(ROOT, p), m: fs.statSync(path.join(ROOT, p)).mtimeMs })
    } catch {
      /* 竞态删除：忽略 */
    }
  }
  all.sort((a, b) => b.m - a.m)
  if (all.length === 0) die('找不到权重文件（tmp/**/weights-*.json）⇒ 用 --weights <path> 指定')
  return all[0]!.p
}

/** fmla 指令条数（有 llvm-objdump 才查；查不到就静默跳过，不影响判定）。 */
function fmlaCount(lib: string): number | null {
  const r = spawnSync('llvm-objdump', ['-d', lib], { encoding: 'utf8', windowsHide: true })
  if (r.status !== 0) return null
  return (r.stdout.match(/\bfmla\b/g) ?? []).length
}

/**
 * 生成设备侧输入：**真实权重**（按 `ensureBlob` 的 blob 顺序）+ 3 组 ±1 随机输入
 * （与 `conv_native_adapter.ts::attest()` 同一 LCG 种子/算法 ⇒ 与生产口径一致）。
 */
async function writeInputs(weights: string, scratch: string): Promise<void> {
  const { buildModelFromText } = await import('../../src/nn/infer.ts')
  const m = buildModelFromText(fs.readFileSync(weights, 'utf8')) as unknown as {
    in16: Float32Array
    stemW: Float32Array
    stemB: Float32Array
    dwW: Float32Array[]
    dwB: Float32Array[]
    pwW: Float32Array[]
    pwB: Float32Array[]
  }
  const buf = (a: Float32Array): Buffer => Buffer.from(a.buffer, a.byteOffset, a.byteLength)
  const wblob = Buffer.concat([m.stemW, m.stemB, ...m.dwW, ...m.dwB, ...m.pwW, ...m.pwB].map(buf))
  fs.writeFileSync(path.join(scratch, 'wblob.bin'), wblob)

  let seed = 20260921 >>> 0
  const rnd = (): number => {
    seed = (seed + 0x6d2b79f5) >>> 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const in16 = m.in16.length === IN_CH * SP
  if (!in16) die(`权重形状不符：in16=${m.in16.length} ≠ ${IN_CH * SP}（需要 h64/d8/19ch）`)
  for (let k = 0; k < 3; k++) {
    const a = new Float32Array(IN_CH * SP)
    for (let i = 0; i < a.length; i++) a[i] = (rnd() - 0.5) * 2
    fs.writeFileSync(path.join(scratch, `in${k}.bin`), buf(a))
  }
}

async function main(): Promise<void> {
  const serial = pickDevice()
  const abi = adb(['-s', serial, 'shell', 'getprop ro.product.cpu.abi'])
  const devAbi = abi.stdout.trim()
  if (abi.status !== 0) die(`设备 ${serial} 不可用：${abi.stderr.trim()}`)
  if (!devAbi.startsWith('arm64')) die(`设备 ${serial} 不是 arm64（ro.product.cpu.abi=${devAbi}）`)

  const newLib = argv('new-lib', NEW_LIB)
  if (!fs.existsSync(newLib))
    die(`缺被测产物 ${newLib}（跑 bun tools/agent/native-build.ts --cross）`)

  const rev = argv('rev', 'HEAD')
  const scratch = path.join(ROOT, argv('out', HOST_SCRATCH))
  fs.mkdirSync(scratch, { recursive: true })
  const oldLib = argv('old-lib')
  const oldPath = path.join(scratch, 'old.so')
  if (oldLib) {
    if (!fs.existsSync(oldLib)) die(`--old-lib 不存在：${oldLib}`)
    fs.copyFileSync(oldLib, oldPath)
  } else {
    gitShow(rev, OLD_LIB_IN_REPO, oldPath)
  }
  fs.copyFileSync(newLib, path.join(scratch, 'new.so'))

  const weights = pickWeights()
  await writeInputs(weights, scratch)
  fs.copyFileSync(DEVICE_SCRIPT, path.join(scratch, 'arm64-prebuilt-probe.py'))

  const ids = fmlaCount(oldPath)
  const newIds = fmlaCount(newLib)
  console.log(
    `[arm64-probe] device=${serial} (${devAbi}) · weights=${path.relative(ROOT, weights)}\n` +
      `[arm64-probe] old=${oldLib || `${rev}:${OLD_LIB_IN_REPO}`} fmla=${ids ?? '?'} · ` +
      `new=${newLib} fmla=${newIds ?? '?'}`,
  )

  const pkg = argv('app', TERMUX_PKG)
  const stage = `/data/local/tmp/${DEVICE_WORK}`
  const push = adb(['-s', serial, 'shell', 'mkdir', '-p', stage])
  if (push.status !== 0) die(`设备建暂存目录失败：${push.stderr.trim()}`)
  const files = [
    'old.so',
    'new.so',
    'wblob.bin',
    'in0.bin',
    'in1.bin',
    'in2.bin',
    'arm64-prebuilt-probe.py',
  ]
  const pushed = adb(
    ['-s', serial, 'push', ...files.map((f) => path.join(scratch, f)), `${stage}/`],
    300_000,
  )
  if (pushed.status !== 0) die(`adb push 失败：${pushed.stderr.trim() || pushed.stdout.trim()}`)

  // run-as 只能进 Termux 自己的 data 目录 ⇒ 先 cp 进去再跑；Termux 的 python3 在 $PREFIX/bin。
  const shellScript = [
    'export HOME=/data/data/com.termux/files/home',
    'export PREFIX=/data/data/com.termux/files/usr',
    'export PATH=$PREFIX/bin:$PATH',
    'export LD_LIBRARY_PATH=$PREFIX/lib',
    'export TMPDIR=$HOME/tmp',
    'mkdir -p $TMPDIR',
    `mkdir -p $HOME/${DEVICE_WORK}`,
    `cd $HOME/${DEVICE_WORK}`,
    `cp ${stage}/old.so ${stage}/new.so ${stage}/wblob.bin ${stage}/in0.bin ${stage}/in1.bin ${stage}/in2.bin ${stage}/arm64-prebuilt-probe.py .`,
    'command -v python3 >/dev/null || { echo NO_PYTHON3; exit 7; }',
    'python3 arm64-prebuilt-probe.py old.so new.so wblob.bin in0.bin in1.bin in2.bin',
  ].join('; ')
  const run = adb(['-s', serial, 'shell', `run-as ${pkg} sh -c '${shellScript}'`], 300_000)
  const out = `${run.stdout}${run.stderr}`.trim()
  if (out.includes('NO_PYTHON3')) {
    die(`设备上没有 python3 ⇒ 先按 tools/agent/agent-setup.md §3 装 Termux（pkg install python）`)
  }
  if (out) console.log(out)

  const attest = /ATTEST=(PASS|FAIL)/.exec(out)?.[1] ?? null
  const ratio = /RATIO ([\d.]+)/.exec(out)?.[1] ?? ''
  if (attest === null) {
    die(`设备侧没有给出 ATTEST 结论（exit=${run.status}）⇒ 看上面输出定位（ABI/尺寸/dlopen）`)
  }
  if (!has('keep')) {
    adb(['-s', serial, 'shell', `run-as ${pkg} sh -c 'rm -rf $HOME/${DEVICE_WORK}'`], 60_000)
    adb(['-s', serial, 'shell', 'rm', '-rf', stage], 60_000)
  }
  console.log(
    `[arm64-probe] ${attest === 'PASS' ? '✅ PASS' : '❌ FAIL'} · 吞吐比 ${ratio || '?'}×` +
      `${attest === 'PASS' ? '' : ' ⇒ arm64 入库产物与参考臂不一致，先别发给节点'}`,
  )
  process.exit(attest === 'PASS' ? 0 : 1)
}

await main()
