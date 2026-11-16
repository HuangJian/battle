/**
 * `replay-to-shard --serve`（长驻池入口）与一次性调用**逐字节等价**。
 *
 * 为什么需要这条（2026-11-16，plan/replay-corpus.plan.md P0 ⑤）：
 * 生产转换原先**每 shard 起一个新进程**，云机实测每个 shard 要付 ~0.45–0.6 s 的启动 + 模块加载
 * （转换本身只 126 ms），350 shard 白烧 130 s ⇒ 700 shard 规模下 P0 的「转换 ≤2 min」差 3 倍。
 * 修法是把转换接进已有的长驻池（`worker/serve_pool.py` ↔ `tools/sim/serve-loop.ts`），
 * 而这引入一个必须钉住的风险：**同一个进程连续转多个 shard，产物必须与逐进程转换完全一致**
 * —— 模块级可变状态、编码器复用、World 没重建，任何一处泄漏都会让语料悄悄变质。
 *
 * 判据：
 *   ① serve 一局的 obs/scalars/extra 与一次性调用**逐字节相同**；
 *   ② 同一个 worker 连跑第二局仍 OK（池的收益就在这里，不许只对第一局正确）；
 *   ③ 未知 mode ⇒ `__SERVE_ERR__`（不静默跑），且 worker 之后照常服务；
 *   ④ 单局失败不杀 worker（失败只报错，进程还活着）。
 */
import { describe, it, expect, beforeAll, afterAll } from 'bun:test'
import { cpSync, mkdtempSync, readFileSync, rmSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const REPO_ROOT = join(import.meta.dir, '..')
const WEIGHTS = 'nn-training/weights/g4-mobility/g4-mobility.it380.20261114-194719.json'
const MAX_TICKS = '600'
const SHARD = 'rl_s0_seed1'
const OBS_FILES = ['obs.npy', 'scalars.npy', 'extra.npy'] as const

let root = ''
let shardA = ''
let shardB = ''
let shardC = ''
let oneShotOut = ''

function runRollout(outDir: string): void {
  const p = Bun.spawnSync(
    [
      'bun',
      'tools/sim/export-rl-rollout.ts',
      '--out',
      outDir,
      '--stages',
      '0',
      '--seeds',
      '1',
      '--max-ticks',
      MAX_TICKS,
      '--lives-override',
      '3',
      '--weights',
      WEIGHTS,
      '--corpus-fmt',
      'replay',
    ],
    { cwd: REPO_ROOT, stdout: 'pipe', stderr: 'pipe' },
  )
  if (p.exitCode !== 0) {
    throw new Error(`rollout exit ${p.exitCode}: ${p.stderr.toString().slice(-800)}`)
  }
}

/** serve 模式的一条任务行 = `['conv', ...该 shard 的 argv]`（与 `serve-any` 的 `[mode, ...argv]` 同规）。 */
function convTask(shardDir: string, outDir: string): string[] {
  return ['conv', '--shard-dir', shardDir, '--out', outDir]
}

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), 'conv-serve-'))
  runRollout(join(root, 'replay'))
  const src = join(root, 'replay', SHARD)
  shardA = join(root, 'a')
  shardB = join(root, 'b')
  shardC = join(root, 'c')
  for (const d of [shardA, shardB, shardC]) cpSync(src, d, { recursive: true })
  oneShotOut = join(root, 'out-oneshot')
  const p = Bun.spawnSync(
    ['bun', 'tools/sim/replay-to-shard.ts', '--shard-dir', shardA, '--out', oneShotOut],
    { cwd: REPO_ROOT, stdout: 'pipe', stderr: 'pipe' },
  )
  if (p.exitCode !== 0) {
    throw new Error(`oneshot convert exit ${p.exitCode}: ${p.stderr.toString().slice(-800)}`)
  }
}, 120_000)

afterAll(() => {
  if (root) rmSync(root, { recursive: true, force: true })
})

describe('replay-to-shard --serve（长驻池入口）', () => {
  it('两局连跑与一次性转换逐字节一致；坏 mode / 坏行只报错不杀 worker', async () => {
    const proc = Bun.spawn([process.execPath, 'tools/sim/replay-to-shard.ts', '--serve'], {
      cwd: REPO_ROOT,
      stdin: 'pipe',
      stdout: 'pipe',
      stderr: 'pipe',
    })
    try {
      const reader = proc.stdout.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      const seen: string[] = []
      const nextLine = async (): Promise<string> => {
        for (;;) {
          const nl = buf.indexOf('\n')
          if (nl >= 0) {
            const line = buf.slice(0, nl).trim()
            buf = buf.slice(nl + 1)
            if (line) {
              seen.push(line)
              return line
            }
            continue
          }
          const { value, done } = await reader.read()
          if (done) throw new Error(`serve stdout 关闭；已见 ${seen.join(' | ')}`)
          buf += decoder.decode(value as Uint8Array)
        }
      }
      /** 只认 `__SERVE_*__` 标记行（转换器自己的 `[replay-to-shard] OK` 混在同一路 stdout）。 */
      const nextMarker = async (): Promise<string> => {
        for (;;) {
          const line = await nextLine()
          if (line.startsWith('__SERVE_')) return line
        }
      }
      const waitOk = async (): Promise<void> => {
        const line = await nextMarker()
        if (line !== '__SERVE_OK__') throw new Error(`serve 未报 OK：${line}`)
      }

      expect(await nextMarker()).toBe('__SERVE_READY__')

      // ① 第一局：与一次性转换逐字节相同
      proc.stdin.write(`${JSON.stringify(convTask(shardB, join(root, 'out-b')))}\n`)
      await waitOk()
      for (const f of OBS_FILES) {
        expect(readFileSync(join(root, 'out-b', f)).equals(readFileSync(join(oneShotOut, f)))).toBe(
          true,
        )
      }

      // ② 同一个 worker 第二局（池的收益全在这里：不重新付启动成本）
      proc.stdin.write(`${JSON.stringify(convTask(shardC, join(root, 'out-c')))}\n`)
      await waitOk()
      for (const f of OBS_FILES) {
        expect(readFileSync(join(root, 'out-c', f)).equals(readFileSync(join(oneShotOut, f)))).toBe(
          true,
        )
      }

      // ③ 未知 mode：响亮报错，worker 不倒
      proc.stdin.write(`${JSON.stringify(['nope', '--shard-dir', shardA])}\n`)
      const errLine = await nextMarker()
      expect(errLine.startsWith('__SERVE_ERR__')).toBe(true)
      expect(errLine.includes('unknown mode')).toBe(true)

      // ④ 坏行：同样只报错
      proc.stdin.write('{not json\n')
      expect(await nextMarker()).toBe('__SERVE_ERR__ bad-json')

      // ⑤ 出过错之后仍能正常服务（第 ⑤ 步 = 第 ③④ 步的反探针）
      proc.stdin.write(`${JSON.stringify(convTask(shardA, join(root, 'out-a2')))}\n`)
      await waitOk()
      expect(
        readFileSync(join(root, 'out-a2', 'obs.npy')).equals(
          readFileSync(join(oneShotOut, 'obs.npy')),
        ),
      ).toBe(true)
      expect(proc.exitCode).toBe(null)
    } finally {
      proc.kill('SIGTERM')
    }
  }, 60_000)
})
