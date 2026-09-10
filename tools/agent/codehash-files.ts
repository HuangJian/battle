/**
 * codehash-files.ts — codeHash 文件集展开（TS 侧；与 nn-training/dist_common.py 逐字节
 * 一致的双语契约，2026-09-01 事故防线）。
 *
 * 从 sampler-agent.ts 抽出（plan/dist-codehash-stale-fix.md F3/F4）：sampler-agent
 * 模块加载有副作用（agent.auth 生成 / workdir 清扫 / console.log 时间戳包装），诊断
 * 工具与单测不应连带触发；且 F3 过滤规则需要一个两侧对拍的纯实现。
 *
 * 本文件**必须在 codehash-files.txt 集内**（它决定 codeHash 的取值规则，不在集内则
 * 单独改动不触发升级波——与 2026-09-01 事故同源的风险）。
 */
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const REPO_ROOT = path.resolve(import.meta.dir, '..', '..')
export const CODE_HASH_MANIFEST = path.join(import.meta.dir, 'codehash-files.txt')

// ---------------- F3 噪声过滤（契约写死在 codehash-files.txt 头部注释） ----------------
/** 目录递归时的跳过规则（F3）：任一路径段以 '.' 开头（隐藏项 .git/.DS_Store/.venv）
 * 或名为 __pycache__ / node_modules。与 dist_common._skip_codehash_dir 逐条对齐。 */
export function isSkippedCodeHashDir(name: string): boolean {
  return name.startsWith('.') || name === '__pycache__' || name === 'node_modules'
}

/** 文件级跳过规则（F3）：隐藏项（. 开头）+ 编辑器/构建临时后缀。
 * 与 dist_common._skip_codehash_file 逐条对齐。 */
export function isSkippedCodeHashFile(name: string): boolean {
  return (
    name.startsWith('.') || /\.(pyc|orig|rej|bak|tmp|log|swp)$/.test(name) || name.endsWith('~')
  )
}

/** 对 entries（posix 相对路径 + 内容）按路径字典序，依次喂 sha256(path)+sha256(content)。 */
export function computeCodeHashFromFiles(entries: { relPath: string; content: Buffer }[]): string {
  const sorted = [...entries].sort((a, b) =>
    a.relPath < b.relPath ? -1 : a.relPath > b.relPath ? 1 : 0,
  )
  const h = createHash('sha256')
  for (const e of sorted) {
    h.update(e.relPath.replace(/\\/g, '/'))
    h.update(createHash('sha256').update(e.content).digest())
  }
  return h.digest('hex')
}

/**
 * 按 SSOT 清单 codehash-files.txt 展开 codeHash 文件集（与 dist_common.py 同源）。
 * 清单每行一个条目：'#' 注释 / 空行忽略；以 '/' 结尾 = 目录（递归，受 F3 过滤）；
 * 其余 = 具体文件（相对 repo 根、posix 路径；不存在则跳过，单文件条目不过滤）。
 * manifestPath 可注入（测试用 fixture 清单）。
 */
export function collectCodeHashEntries(
  manifestPath: string = CODE_HASH_MANIFEST,
): { relPath: string; content: Buffer }[] {
  const out: { relPath: string; content: Buffer }[] = []
  let text = ''
  try {
    text = fs.readFileSync(manifestPath, 'utf8')
  } catch {
    return out
  }
  const walk = (dir: string): void => {
    if (!fs.existsSync(dir)) return
    for (const name of fs.readdirSync(dir)) {
      if (isSkippedCodeHashDir(name)) continue
      const p = path.join(dir, name)
      const st = fs.statSync(p)
      if (st.isDirectory()) walk(p)
      else if (!isSkippedCodeHashFile(name))
        out.push({ relPath: path.relative(REPO_ROOT, p), content: fs.readFileSync(p) })
    }
  }
  for (const raw of text.split(/\r?\n/)) {
    const spec = raw.trim()
    if (!spec || spec.startsWith('#')) continue
    const s = spec.replace(/\\/g, '/')
    if (s.endsWith('/')) walk(path.join(REPO_ROOT, ...s.slice(0, -1).split('/')))
    else {
      const p = path.join(REPO_ROOT, ...s.split('/'))
      if (fs.existsSync(p) && fs.statSync(p).isFile())
        out.push({ relPath: path.relative(REPO_ROOT, p), content: fs.readFileSync(p) })
    }
  }
  // relPath 归一化正斜杠——Windows 上的 self agent 用 path.relative 会产出反斜杠，
  // 与 Python 侧（已归一化）哈希不一致 ⇒ self 永远 codeHash 红姻。
  for (const e of out) e.relPath = e.relPath.replace(/\\/g, '/')
  return out
}

export function computeCodeHash(manifestPath?: string): string {
  return computeCodeHashFromFiles(collectCodeHashEntries(manifestPath))
}

// ---------------- engine_epoch gameplay 文件集（EvalBench §2.5） ----------------
/**
 * gameplay 文件集（EvalBench §2.5 唯一源）：src/game/** + src/config/** +
 * src/utils/**（含 RNG）+ src/ai/**（God AI）+ tools/sim/export-eval-game.ts；
 * God 侧借用 freeze golden tools/det-golden.v1.sha256 作行为指纹分量。
 * 目录条目递归（F3 过滤同 codeHash）；单文件条目（'/' 不结尾）直接纳入。
 * 本表与配方都在集内文件 ⇒ 改表/改配方即触发节点升级波（fail-closed 前提）。
 */
export const GAMEPLAY_SPECS: readonly string[] = [
  'src/game/',
  'src/config/',
  'src/utils/',
  'src/ai/',
  'tools/sim/export-eval-game.ts',
  'tools/det-golden.v1.sha256',
]

/** 按 spec 表展开文件集（root 缺省仓库根；排序确定性；缺失条目跳过）。 */
export function collectSpecEntries(
  specs: readonly string[],
  root: string = REPO_ROOT,
): { relPath: string; content: Buffer }[] {
  const out: { relPath: string; content: Buffer }[] = []
  const walk = (dir: string): void => {
    if (!fs.existsSync(dir)) return
    for (const name of [...fs.readdirSync(dir)].sort()) {
      if (isSkippedCodeHashDir(name)) continue
      const p = path.join(dir, name)
      const st = fs.statSync(p)
      if (st.isDirectory()) walk(p)
      else if (!isSkippedCodeHashFile(name))
        out.push({
          relPath: path.relative(root, p).replace(/\\/g, '/'),
          content: fs.readFileSync(p),
        })
    }
  }
  for (const raw of specs) {
    const s = raw.replace(/\\/g, '/')
    if (s.endsWith('/')) walk(path.join(root, ...s.slice(0, -1).split('/')))
    else {
      const p = path.join(root, ...s.split('/'))
      if (fs.existsSync(p) && fs.statSync(p).isFile())
        out.push({
          relPath: path.relative(root, p).replace(/\\/g, '/'),
          content: fs.readFileSync(p),
        })
    }
  }
  return [...new Map(out.map((e) => [e.relPath, e])).values()].sort((a, b) =>
    a.relPath < b.relPath ? -1 : a.relPath > b.relPath ? 1 : 0,
  )
}

/** gameplay 文件集指纹（与 dist codeHash 同配方）。 */
export function gameplayFingerprint(root: string = REPO_ROOT): string {
  return computeCodeHashFromFiles(collectSpecEntries(GAMEPLAY_SPECS, root))
}

/** 诊断报告（F4，plan/dist-codehash-stale-fix.md）：`sha8\tsize\trelPath` 按 relPath
 * 排序，末行 `codeHash=<full>`——与 dist_common.code_hash_report() 同格式，双侧 diff
 * 直接看出多/少/改。 */
export function codeHashReport(manifestPath?: string): string {
  const entries = collectCodeHashEntries(manifestPath)
  const sorted = [...entries].sort((a, b) =>
    a.relPath < b.relPath ? -1 : a.relPath > b.relPath ? 1 : 0,
  )
  const lines = sorted.map(
    (e) =>
      `${createHash('sha256').update(e.content).digest('hex').slice(0, 8)}\t${e.content.length}\t${e.relPath}`,
  )
  lines.push(`codeHash=${computeCodeHashFromFiles(sorted)}`)
  return lines.join('\n')
}
