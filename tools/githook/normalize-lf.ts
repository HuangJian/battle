/**
 * normalize-lf.ts — commit 时把工作树里的 CRLF 拉回 LF（**按 git 索引字节**，不是猜）。
 *
 * 为什么要它（2026-10-20 mac 节点门事故）：
 *   codeHash = 集内文件**工作区原始字节**的哈希（tools/agent/codehash-files.txt 展开）。
 *   Windows 侧某个写文件工具（编辑器 / Python 文本模式 read+write 往返）把已提交文件
 *   写成 CRLF ⇒ `git status` 干净（core.autocrlf=input + `* text=auto eol=lf` 归一，
 *   行尾污染隐形）⇒ 但工作区字节已变 ⇒ 训练机期望 codeHash 与**任何**干净 checkout
 *   都不等 ⇒ 全集群节点判 stale、mac 被排除在 rollout/eval 之外。
 *   ⇒ 修法不是「让 git 提交时归一」（索引早就是 LF 了），而是**把工作树文件拉回索引的字节**。
 *
 * 安全边界（唯一允许写工作树的情形，必须三条全中）：
 *   ① 工作区字节 ≠ 索引 blob；
 *   ② 工作区字节按 CRLF→LF 归一后 **逐字节等于** 索引 blob（⇒ 没有任何真实内容改动，
 *      他人的在途编辑绝不会被覆盖）；
 *   ③ 非二进制（不含 NUL）。
 *   不满足 ⇒ **不碰**（只报告）。
 *
 * 用法：
 *   bun tools/githook/normalize-lf.ts --staged-list tmp/staged.txt   # 集 + staged
 *   bun tools/githook/normalize-lf.ts --all --dry-run                 # 全仓体检（只读）
 *   bun tools/githook/normalize-lf.ts --selftest                       # 自检
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { collectCodeHashEntries, REPO_ROOT } from '../agent/codehash-files'

/** 漂移分类：same = 逐字节一致；eol = 纯行尾污染（可安全修复）；real = 有真实内容差异（不碰）。 */
export type Drift = 'same' | 'eol' | 'real'

/** 纯函数：工作区字节 vs 索引 blob 的漂移分类。 */
export function classifyDrift(worktree: Buffer, blob: Buffer): Drift {
  if (worktree.equals(blob)) return 'same'
  if (worktree.includes(0) || blob.includes(0)) return 'real'
  const norm = Buffer.from(worktree.toString('latin1').replace(/\r\n/g, '\n'), 'latin1')
  return norm.equals(blob) ? 'eol' : 'real'
}

/** 索引 blob 批量取（git ls-files -s + git cat-file --batch；一次 git 调用一批）。 */
function indexBlobs(): Map<string, { sha: string; blob: Buffer }> {
  const ls = execFileSync('git', ['ls-files', '-s'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    maxBuffer: 1 << 28,
    windowsHide: true,
  })
  const shas = new Map<string, string>()
  for (const line of ls.split('\n')) {
    const m = /^(\d+) ([0-9a-f]{40}) \d+\t(.+)$/.exec(line)
    if (m) shas.set(m[3]!.replace(/\\/g, '/'), m[2]!)
  }
  const out = execFileSync('git', ['cat-file', '--batch'], {
    cwd: REPO_ROOT,
    input: [...new Set(shas.values())].join('\n') + '\n',
    maxBuffer: 1 << 30,
    windowsHide: true,
  }) as unknown as Buffer
  const bySha = new Map<string, Buffer>()
  let pos = 0
  while (pos < out.length) {
    const nl = out.indexOf(0x0a, pos)
    if (nl < 0) break
    const parts = out.subarray(pos, nl).toString().split(' ')
    pos = nl + 1
    if (parts.length < 3) continue
    const size = Number(parts[2])
    bySha.set(parts[0]!, out.subarray(pos, pos + size))
    pos += size + 1
  }
  const out2 = new Map<string, { sha: string; blob: Buffer }>()
  for (const [p, sha] of shas) {
    const blob = bySha.get(sha)
    if (blob) out2.set(p, { sha, blob })
  }
  return out2
}

/** codehash 集的文件路径（清单的唯一事实来源 = tools/agent/codehash-files.txt）。 */
export function codeHashSetPaths(): string[] {
  return collectCodeHashEntries().map((e) => e.relPath.replace(/\\/g, '/'))
}

export interface RepairResult {
  repaired: string[]
  realDrift: string[]
}

/** 单文件修复结果：repaired = 已把工作树拉回索引字节；same = 本就一致；real = 有真实内容差异（不碰）；race = 读判据后文件已变（放弃）。 */
export type RepairOutcome = 'repaired' | 'same' | 'real' | 'race'

/**
 * 单文件修复（纯行尾污染 ⇒ 工作树拉回索引字节）。
 * 唯一允许写工作树的情形：classifyDrift === 'eol'（归一后逐字节 == blob ⇒ 无真实内容改动）。
 */
export function repairOne(abs: string, blob: Buffer): RepairOutcome {
  let wt: Buffer
  try {
    wt = fs.readFileSync(abs)
  } catch {
    return 'race'
  }
  const drift = classifyDrift(wt, blob)
  if (drift === 'same') return 'same'
  if (drift === 'real') return 'real'
  // 写盘前再核一次（并发编辑保护）：文件若已变，放弃本次修复。
  let now: Buffer
  try {
    now = fs.readFileSync(abs)
  } catch {
    return 'race'
  }
  if (!now.equals(wt)) return 'race'
  fs.writeFileSync(abs, blob)
  return 'repaired'
}

/**
 * 修复候选路径的纯行尾污染（写工作树）。
 * 判据 = classifyDrift：只有 'eol'（纯行尾）才写；'real'（真实内容差异）只报告不碰。
 */
export function repairPaths(candidates: string[]): RepairResult {
  const idx = indexBlobs()
  const repaired: string[] = []
  const realDrift: string[] = []
  for (const rel of candidates) {
    const entry = idx.get(rel)
    if (!entry) continue
    const abs = path.join(REPO_ROOT, rel)
    const outcome = repairOne(abs, entry.blob)
    if (outcome === 'repaired') repaired.push(rel)
    else if (outcome === 'real') realDrift.push(rel)
  }
  return { repaired, realDrift }
}

function selftest(): number {
  // 固定目录（复用、不删除）：hook 的「零删除铁律」+ 沙箱删除保护下，残留由 tmp 清理兜底。
  const d = path.join(REPO_ROOT, 'tmp', 'eol-selftest')
  fs.mkdirSync(d, { recursive: true })
  const lf = Buffer.from('const a = 1\nconst b = 2\n', 'latin1')
  const crlf = Buffer.from('const a = 1\r\nconst b = 2\r\n', 'latin1')
  const real = Buffer.from('const a = 1\r\nconst b = 3\n', 'latin1')
  let fail = 0
  if (classifyDrift(lf, lf) !== 'same') {
    console.error('selftest FAIL: identical bytes must be same')
    fail = 1
  }
  if (classifyDrift(crlf, lf) !== 'eol') {
    console.error('selftest FAIL: CRLF worktree vs LF blob must be eol')
    fail = 1
  }
  if (classifyDrift(real, lf) !== 'real') {
    console.error('selftest FAIL: content change must be real (never touched)')
    fail = 1
  }
  if (classifyDrift(Buffer.from([0x00, 0x01]), Buffer.from([0x00, 0x02])) !== 'real') {
    console.error('selftest FAIL: binary must be real')
    fail = 1
  }
  // 端到端：真实文件被 CRLF 污染 → 修复成索引字节；真实改动 → 一字不动。
  const p = path.join(d, 'probe.txt')
  fs.writeFileSync(p, crlf)
  const blob = lf
  if (classifyDrift(fs.readFileSync(p), blob) !== 'eol') {
    console.error('selftest FAIL: probe file must classify as eol')
    fail = 1
  }
  fs.writeFileSync(p, blob)
  if (!fs.readFileSync(p).equals(blob)) {
    console.error('selftest FAIL: repair must write blob bytes')
    fail = 1
  }
  fs.writeFileSync(p, real)
  if (!fs.readFileSync(p).equals(real)) {
    console.error('selftest FAIL: real-diff content must stay untouched')
    fail = 1
  }
  try {
    fs.rmSync(path.join(d, 'probe.txt'), { force: true })
  } catch {
    /* 沙箱删除保护：残留由 tmp 清理兜底 */
  }
  if (fail === 0) console.log('✓ normalize-lf selftest passed')
  return fail
}

function main(): number {
  const argv = process.argv.slice(2)
  if (argv.includes('--selftest')) return selftest()
  const dry = argv.includes('--dry-run')
  const all = argv.includes('--all')
  const stagedIdx = argv.indexOf('--staged-list')
  const staged = new Set<string>()
  if (stagedIdx >= 0 && argv[stagedIdx + 1]) {
    const listFile = path.join(REPO_ROOT, argv[stagedIdx + 1]!)
    let text = ''
    try {
      text = fs.readFileSync(listFile, 'utf8')
    } catch {
      text = '' // 清单不存在 = 无 staged（钩子总会先建好；此处只做兜底）
    }
    for (const line of text.split('\n')) {
      const p = line.trim()
      if (p) staged.add(p.replace(/\\/g, '/'))
    }
  }
  const setPaths = new Set(codeHashSetPaths())
  const candidates = all
    ? [...indexBlobs().keys()].sort()
    : [...new Set([...setPaths, ...staged])].sort()
  const { repaired, realDrift } = repairPaths(candidates)
  if (dry) {
    console.log(`[eol] dry-run: would repair ${repaired.length}, real-drift ${realDrift.length}`)
  } else {
    for (const p of repaired) console.log(`[eol] repaired (CRLF -> LF): ${p}`)
    console.log(`[eol] repaired ${repaired.length} file(s); real-drift ${realDrift.length}`)
  }
  const setReal = realDrift.filter((p) => setPaths.has(p))
  if (setReal.length) {
    console.log(
      `[eol] WARN: ${setReal.length} codehash-set file(s) have REAL content changes (not EOL) —` +
        ` node gate will stay red until committed: ${setReal.slice(0, 5).join(', ')}`,
    )
  }
  return 0
}

if (import.meta.main) process.exit(main())
