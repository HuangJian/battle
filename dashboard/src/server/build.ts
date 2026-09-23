/** build.ts — 控制台客户端 bundle 构建（独立于 vite.config.ts，R13 隔离三件套之一）。
 *
 *  ensureBundle(target)：mtime 失效检测（dashboard/src/web/** 任一源新于产物 → 重建，
 *  ~300ms，打印一行日志）→ Bun.build → 禁词断言（客户端不得含 node:/fs/Bun./api/actions）
 *  → gzip 体积断言（< 150KB 硬门禁，超预算构建失败）。日常（不改 UI）冷启动构建一次。
 *
 *  --analyze 模式：输出各模块体积 top10（经 inline sourcemap 的 sourcesContent 长度）。
 */

import { build } from 'bun'
import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'fs'
import path from 'path'
import { BUNDLE_DIR, DASHBOARD_ROOT } from '../core/paths'

/** 客户端源码根（dashboard/src/web/**：共享视图层 + 浏览器 app）。 */
const WEB_DIR = path.join(DASHBOARD_ROOT, 'src', 'web')
export const BUNDLE_GZIP_BUDGET = 150 * 1024

export interface BundleTarget {
  key: string
  entry: string
  out: string
}

export const BUNDLES: BundleTarget[] = [
  {
    key: 'app',
    entry: path.join(WEB_DIR, 'app', 'index.tsx'),
    out: path.join(BUNDLE_DIR, 'app.js'),
  },
  {
    key: 'log',
    entry: path.join(WEB_DIR, 'app', 'log-index.tsx'),
    out: path.join(BUNDLE_DIR, 'log.js'),
  },
  {
    key: 'eval',
    entry: path.join(WEB_DIR, 'app', 'eval-index.tsx'),
    out: path.join(BUNDLE_DIR, 'eval.js'),
  },
]

/** 客户端禁词（分层铁律 R1）：打到 bundle 文本里的服务端能力即违规。 */
const FORBIDDEN: RegExp[] = [
  /node:/,
  /\bBun\./,
  /require\(/,
  /\/server\/(api|actions)/,
  /readFileSync|readdirSync|writeFileSync|statSync|existsSync|openSync/,
  /import\s*\{.*\}\s*from\s+['"]fs['"]/,
]

export interface EnsureResult {
  path: string
  rebuilt: boolean
  bytes: number
  gzipBytes: number
}

function walkTs(dir: string, out: string[]): string[] {
  let names: string[] = []
  try {
    names = readdirSync(dir, { withFileTypes: true }).map((d) => d.name)
  } catch {
    return out
  }
  for (const name of names) {
    if (name === '.build' || name === 'node_modules') continue
    const p = path.join(dir, name)
    try {
      if (statSync(p).isDirectory()) walkTs(p, out)
      else if (name.endsWith('.ts') || name.endsWith('.tsx')) out.push(p)
    } catch {
      /* stat race */
    }
  }
  return out
}

function newestSourceMtime(): number {
  const files = walkTs(WEB_DIR, [])
  let newest = 0
  for (const f of files) {
    try {
      newest = Math.max(newest, statSync(f).mtimeMs)
    } catch {
      /* skip */
    }
  }
  return newest
}

async function doBuild(target: BundleTarget, analyze: boolean): Promise<Uint8Array> {
  const out = await build({
    entrypoints: [target.entry],
    outdir: path.dirname(target.out),
    naming: path.basename(target.out),
    minify: false,
    sourcemap: analyze ? 'inline' : 'none',
    define: {
      'process.env.NODE_ENV': '"production"',
    },
  })
  if (!out.success) {
    for (const log of out.logs) console.error(log)
    throw new Error(`bundle ${target.key} 构建失败（AGENTS §9：三份 bundle 必须全部构建通过）:`)
  }
  const artifact = out.outputs[0]
  if (!artifact) throw new Error(`bundle ${target.key} 无产物`)
  const bytes = await artifact.arrayBuffer()
  const raw = new Uint8Array(bytes)

  const text = new TextDecoder().decode(raw)
  for (const re of FORBIDDEN) {
    if (re.test(text)) {
      throw new Error(
        `bundle ${target.key} 含禁词 ${re.toString()} —— 客户端引入了服务端能力，违反分层铁律` +
          `（规则：AGENTS §9 / §3「dashboard 只 import 各目录 index.ts 桶」；细节：docs/agents.details.md §9）`,
      )
    }
  }
  if (analyze) {
    const m = text.match(/sourceMappingURL=data:application\/json;base64,([A-Za-z0-9+/=]+)/)
    if (m) {
      const sm = JSON.parse(atob(m[1]!)) as { sources?: string[]; sourcesContent?: string[] }
      const sizes = (sm.sources ?? [])
        .map((src, i) => ({ src, bytes: (sm.sourcesContent?.[i] ?? '').length }))
        .sort((a, b) => b.bytes - a.bytes)
        .slice(0, 10)
      console.log(`[build:analyze] ${target.key} 模块体积 top10:`)
      for (const s of sizes) {
        const rel = s.src.includes('node_modules')
          ? (s.src.split('node_modules')[1] ?? s.src)
          : s.src
        console.log(`  ${String(s.bytes).padStart(9)}  ${rel}`)
      }
    }
  }
  return raw
}

/** mtime 失效 + 构建 + 禁词/体积断言。返回产物路径。 */
export async function ensureBundle(
  target: BundleTarget,
  opts: { analyze?: boolean; force?: boolean } = {},
): Promise<EnsureResult> {
  if (!existsSync(target.out) || opts.force || newestSourceMtime() > statSync(target.out).mtimeMs) {
    const start = Date.now()
    const raw = await doBuild(target, !!opts.analyze)
    mkdirSync(path.dirname(target.out), { recursive: true })
    writeFileSync(target.out, raw)
    const gzipBytes = Bun.gzipSync(new Uint8Array(raw)).byteLength
    if (gzipBytes > BUNDLE_GZIP_BUDGET) {
      throw new Error(
        `bundle ${target.key} gzip ${(gzipBytes / 1024).toFixed(0)}KB 超预算 ${Math.round(BUNDLE_GZIP_BUDGET / 1024)}KB —— 硬门禁` +
          `（规则：AGENTS §9 物化于 dashboard 的 bundle 预算；细节：docs/agents.details.md §9）`,
      )
    }
    console.log(
      `[build] ${target.key} rebuilt in ${Date.now() - start}ms (${raw.byteLength}B / gzip ${gzipBytes}B)`,
    )
    return { path: target.out, rebuilt: true, bytes: raw.byteLength, gzipBytes }
  }
  const st = statSync(target.out)
  return {
    path: target.out,
    rebuilt: false,
    bytes: st.size,
    gzipBytes: Bun.gzipSync(readFileSync(target.out)).byteLength,
  }
}

// ────────────────────────── CLI（--analyze / 手动重建） ──────────────────────────

if (import.meta.main) {
  const analyze = process.argv.includes('--analyze')
  const only = process.argv.filter((a) => !a.startsWith('-')).slice(2)
  const targets = only.length > 0 ? BUNDLES.filter((b) => only.includes(b.key)) : BUNDLES
  for (const t of targets) {
    await ensureBundle(t, { analyze, force: true })
  }
  console.log('[build] all bundles ok')
}
