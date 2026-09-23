/**
 * measure-suite.ts — 根套件「每文件墙钟」剖面 + HEAVY_TESTS 判定。
 *
 * 为什么要有这个工具：`tools/test-silent.ts` 的 HEAVY_TESTS 注释长期写着
 * 「keep in sync with measured wall-time (see per-file profiling)」——但那份
 * **profiling 并不存在**，于是两个条目都腐烂了（记的 ~19.5s / ~2.5s，实测
 * 12.9s / 0.67s；calibration 的 heavy 身份是 full-sweep 移交给 CLI 之前的残留）。
 * 把「重新测量」变成一条命令，才能让那份名单有可复核的依据。
 *
 * 判据（见 tools/test-silent.ts 的 HEAVY_TESTS 注释）：
 *   **只排除「单跑墙钟 ≥ 整份非 heavy 套件墙钟」的文件** —— 它一个就抵得上整份套件。
 *   低于这条线时 `--parallel` 会把它填进既有尾巴，剔除收益 ≈ 它自己的运行时间，却
 *   白送一个静默盲区（CI 无根套件 workflow ⇒ 本地 hook 是它唯一的自动化通道）。
 *
 * 用法：
 *   bun tools/measure-suite.ts            # 全部文件排序输出 + 条目判定
 *   bun tools/measure-suite.ts --top 20   # 只打印前 20 行
 *
 * 只读：只跑测试，不写任何文件。
 */
import { CWD, spawnCapture } from './runner'
import { HEAVY_TESTS, allTestFiles, heavyName, isHeavyFile } from './test-silent'

const TOP = readTop(process.argv.slice(2))

/** `--top N` → 只打印 N 行（默认全打）。 */
function readTop(argv: string[]): number {
  const i = argv.indexOf('--top')
  if (i === -1) return Number.POSITIVE_INFINITY
  const n = Number.parseInt(argv[i + 1] ?? '', 10)
  return Number.isFinite(n) && n > 0 ? n : Number.POSITIVE_INFINITY
}

/**
 * 单文件墙钟（ms）：与真实调用同 flag（`--timeout=50000`，见 AGENTS §5 / details §5.4）。
 * 返回 exit code —— 非 0 说明该文件本身是红的，它的「快」不算数（剖面前提是全绿）。
 */
async function timeFile(file: string): Promise<{ ms: number; code: number }> {
  const t0 = performance.now()
  const { code } = await spawnCapture('bun', ['test', '--parallel', '--timeout=50000', file], CWD)
  return { ms: Math.round(performance.now() - t0), code }
}

/** 整份套件的墙钟（ms）——判据里的「地板」。 */
async function timeSuite(files: string[]): Promise<number> {
  const t0 = performance.now()
  await spawnCapture('bun', ['test', '--parallel', '--timeout=50000', ...files], CWD)
  return Math.round(performance.now() - t0)
}

const all = allTestFiles(CWD)
const nonHeavy = all.filter((f) => !isHeavyFile(f))

console.log(`▸ profiling ${all.length} 个测试文件（单跑计时）+ 套件地板…`)
console.log()

const suiteMs = await timeSuite(nonHeavy)

const measured: { file: string; ms: number; code: number }[] = []
for (const f of all) {
  const { ms, code } = await timeFile(f)
  measured.push({ file: f, ms, code })
}
measured.sort((a, b) => b.ms - a.ms)

const failed = measured.filter((m) => m.code !== 0)
if (failed.length > 0) {
  console.log(
    `⚠ ${failed.length} 个文件单跑非 0 —— 下面的耗时对它们不可信（先治红再用本剖面定名单）：`,
  )
  for (const m of failed) console.log(`    ${m.file} (exit ${m.code})`)
  console.log()
}

const W = String(measured[0]?.ms ?? 0).length + 2
console.log(`=== 每文件墙钟（降序${TOP === Number.POSITIVE_INFINITY ? '' : `，前 ${TOP}`}）===`)
for (const m of measured.slice(0, TOP)) {
  const tag = isHeavyFile(m.file) ? '  [HEAVY_TESTS]' : ''
  console.log(`  ${String(m.ms).padStart(W)}ms  ${m.file}${tag}`)
}
console.log()
console.log(`=== 地板 ===`)
console.log(`  非 heavy 全量套件（${nonHeavy.length} files, --parallel）: ${suiteMs}ms`)
console.log()

// 判定：每个条目是否配得上「排除」——单跑 ≥ 地板。
console.log(`=== HEAVY_TESTS 判定（判据：单跑墙钟 ≥ 套件地板）===`)
for (const name of [...HEAVY_TESTS].sort()) {
  const hit = measured.find((m) => heavyName(m.file) === name)
  if (!hit) {
    console.log(
      `  ✗ ${name.padEnd(20)} 找不到对应文件 —— 死条目，删掉（tests/test-silent-scope.test.ts 会红）`,
    )
    continue
  }
  const ratio = hit.ms / suiteMs
  if (ratio >= 1) {
    console.log(
      `  ✓ ${name.padEnd(20)} ${hit.ms}ms / 地板 ${suiteMs}ms = ${ratio.toFixed(2)}×  → 达标，保留`,
    )
  } else {
    console.log(
      `  ✗ ${name.padEnd(20)} ${hit.ms}ms / 地板 ${suiteMs}ms = ${ratio.toFixed(2)}×  → ` +
        `不达标：剔除收益 ≤ ${hit.ms}ms（噪声带内），却白送一个静默盲区`,
    )
  }
}

// 也报告「没进名单但接近地板」的候选，免得下次只盯旧条目。
const near = measured.filter((m) => !isHeavyFile(m.file) && m.ms >= suiteMs * 0.5).slice(0, 8)
if (near.length > 0) {
  console.log()
  console.log(`=== 未入名单但已达地板 50%+ 的量级（重测量时优先看这些）===`)
  for (const m of near) console.log(`  ${String(m.ms).padStart(W)}ms  ${m.file}`)
}
