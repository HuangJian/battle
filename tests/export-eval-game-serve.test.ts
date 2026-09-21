import { describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { unpackContainer } from '../tools/sim/pack-container'
import { main } from '../tools/sim/export-eval-game'

/**
 * `export-eval-game.ts --serve`：让 eval 也进 agent 的 persist 池（长驻 worker）。
 *
 * 为什么（docs/nn.progress.md §126 / plan/rollout-eval-opt.plan.md §4.1）：eval 原先每局
 * `spawn` 一个新 bun，a95(Termux) 实测这一下 ~2.5s（一局游戏本身才 ~1s），而且会把 agent
 * 的事件循环占满（任务中 `/v1/status` 首轮应答被拖 2.59s）。协议与 export-rl-rollout 的
 * `--serve` 一字不差：stdin 每行 = 一个任务的 argv（JSON 数组，**不含入口路径**——agent 侧
 * `args.slice(1)`），跑完 `__SERVE_OK__` / 失败 `__SERVE_ERR__ <msg>`。
 *
 * 判据（只用**一个**子进程，基线在进程内跑，整块 <2s）：① READY/OK 握手；② serve 产物与
 * 一次性调用等价（除墙钟 elapsedSec）；③ 坏行响亮报错且 worker 继续服务；④ 同 worker 换
 * seed 的第二局按本局成包（不串局、worker 仍活着）。
 */
const REPO_ROOT = join(import.meta.dir, '..')
const MAX_TICKS = '120' // 瘦身局：本测只钉容器接线，不测玩法

function slimWeights(dir: string): string {
  const g = JSON.parse(
    readFileSync(join(REPO_ROOT, 'tests', 'fixtures', 'student-golden.json'), 'utf8'),
  ) as { h: number; d: number; params: Record<string, unknown> }
  const p = join(dir, 'weights.json')
  writeFileSync(p, JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params }))
  return p
}

/** 一次性调用的 argv（含入口路径；serve 送的是 slice(1)）。 */
function taskArgs(weights: string, out: string, pack: string, seed: number): string[] {
  return [
    'tools/sim/export-eval-game.ts',
    '--weights',
    weights,
    '--out',
    out,
    '--stage',
    '0',
    '--seed',
    String(seed),
    '--max-ticks',
    MAX_TICKS,
    '--difficulty',
    'hard',
    '--wver',
    'serve-test',
    '--node-label',
    'serve-test',
    '--lives-override',
    '1',
    '--pack',
    pack,
  ]
}

function readManifest(pack: string): Record<string, unknown> {
  const { manifest, entries } = unpackContainer(readFileSync(pack))
  expect(entries.size).toBe(0) // eval 容器无 shards（只带 _eval_report 的字段）
  return manifest
}

/** 除墙钟 elapsedSec 外逐字段相同。 */
function sameReport(a: Record<string, unknown>, b: Record<string, unknown>): void {
  const strip = (m: Record<string, unknown>): Record<string, unknown> => {
    const { elapsedSec: _drop, ...rest } = m
    return rest
  }
  expect(strip(b)).toEqual(strip(a))
}

describe('export-eval-game --serve', () => {
  it('握手 + 与一次性调用等价 + 坏行不致命 + 跨局不串', async () => {
    if (!existsSync(join(REPO_ROOT, 'tests', 'fixtures', 'student-golden.json'))) {
      throw new Error('missing tests/fixtures/student-golden.json')
    }
    const base = join(REPO_ROOT, 'tmp')
    mkdirSync(base, { recursive: true })
    const work = mkdtempSync(join(base, 'eval-serve-'))
    const weights = slimWeights(work)

    // ---- 基线：进程内直接调 main（省一次 bun 冷启动，整测只留一个子进程）----
    const oneShot = taskArgs(weights, join(work, 'oneshot'), join(work, 'oneshot.pack'), 11)
    main(oneShot.slice(1))
    const expected = readManifest(join(work, 'oneshot.pack'))
    expect(expected.mode).toBe('eval')

    // ---- 唯一子进程：serve 模式连跑两局 ----
    const proc = Bun.spawn([process.execPath, 'tools/sim/export-eval-game.ts', '--serve'], {
      cwd: REPO_ROOT,
      stdin: 'pipe',
      stdout: 'pipe',
      stderr: 'pipe',
    })
    try {
      const reader = proc.stdout.getReader()
      const seen: string[] = []
      const decoder = new TextDecoder()
      let buf = ''
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
      /** 只认 `__SERVE_*__` 标记行（main 自己的汇总日志会混在同一路 stdout —— agent 侧同规）。 */
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

      // ① 与一次性调用等价（除 elapsedSec）
      const a = taskArgs(weights, join(work, 'a'), join(work, 'a.pack'), 11)
      proc.stdin.write(JSON.stringify(a.slice(1)) + '\n')
      await waitOk()
      sameReport(expected, readManifest(join(work, 'a.pack')))

      // ② 坏行：响亮报错，worker 不倒
      proc.stdin.write('{not json\n')
      expect(await nextMarker()).toBe('__SERVE_ERR__ bad-json')

      // ③ 换 seed 的第二局：按本局成包、worker 仍活着
      const b = taskArgs(weights, join(work, 'b'), join(work, 'b.pack'), 12)
      proc.stdin.write(JSON.stringify(b.slice(1)) + '\n')
      await waitOk()
      expect(readManifest(join(work, 'b.pack')).seed).toBe(12)
      expect(proc.exitCode).toBe(null)
    } finally {
      proc.kill('SIGTERM')
      rmSync(work, { recursive: true, force: true })
    }
  }, 30_000)
})
