/**
 * Generic process + git helpers, adapted for the Battle City Web project.
 *
 * This file was copied from a pnpm monorepo where it built a PATH out of every
 * workspace package's `node_modules/.bin`. Battle City Web is a *single* package,
 * so that monorepo logic (WORKSPACE_PKGS) has been removed — only the project
 * root `node_modules/.bin` is added to PATH, and the separator is platform aware
 * (':' on POSIX, ';' on Windows) so `bun` is found correctly.
 */
import { spawn, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const CWD = resolve(import.meta.dir, '..')

// Single-package project: just the local node_modules/.bin on PATH.
const LOCAL_BIN = resolve(CWD, 'node_modules/.bin')
const PATH_SEP = process.platform === 'win32' ? ';' : ':'
const ENV = {
  ...process.env,
  PATH: [LOCAL_BIN, process.env.PATH ?? ''].join(PATH_SEP),
}

export interface Result {
  name: string
  ok: boolean
  ms: number
  output: string
  summary?: string
}

export async function spawnCapture(
  cmd: string,
  args: string[],
  cwd: string,
  timeoutMs = 0,
): Promise<{ code: number; output: string }> {
  return new Promise((res) => {
    const child = spawn(cmd, args, {
      cwd,
      env: ENV,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const chunks: Buffer[] = []
    let killed = false
    const timer =
      timeoutMs > 0
        ? setTimeout(() => {
            killed = true
            child.kill('SIGKILL')
          }, timeoutMs)
        : null

    child.stdout?.on('data', (c: Buffer) => chunks.push(c))
    child.stderr?.on('data', (c: Buffer) => chunks.push(c))
    child.on('error', (err) => {
      if (timer) clearTimeout(timer)
      res({ code: 1, output: `Failed to spawn ${cmd}: ${err.message}` })
    })
    child.on('close', (code) => {
      if (timer) clearTimeout(timer)
      res({ code: killed ? 124 : (code ?? 1), output: Buffer.concat(chunks).toString() })
    })
  })
}

export function printResult(r: Result): void {
  const icon = r.ok ? '✓' : '✗'
  const secs = (r.ms / 1000).toFixed(1)
  const summary = r.summary ? `  ${r.summary}` : ''
  console.log(`${icon} ${r.name} (${secs}s)${summary}`)
  if (r.output && !r.ok) {
    const truncated = tailBytes(r.output, 4000)
    for (const line of truncated.split('\n')) {
      console.log(`  ${line}`)
    }
    console.log('')
  }
}

function tailBytes(s: string, n: number): string {
  if (s.length <= n) return s
  return `…\n${s.slice(-n)}`
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function sha256File(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

/**
 * Repo-relative paths of files that differ from HEAD: working-tree changes,
 * staged changes, and untracked files. Returns [] if git is unavailable
 * (e.g. a corrupted HEAD) so callers can fall back gracefully.
 */
export function gitChangedFiles(cwd: string): string[] {
  try {
    const run = (args: string[]): string[] =>
      execFileSync('git', args, { cwd, encoding: 'utf8' })
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean)
    const modified = run(['diff', '--name-only', 'HEAD'])
    const staged = run(['diff', '--cached', '--name-only'])
    const untracked = run(['ls-files', '--others', '--exclude-standard'])
    return [...new Set([...modified, ...staged, ...untracked])]
  } catch {
    return []
  }
}

// Re-export so sibling tooling can resolve the project root without recomputing.
export { CWD }
