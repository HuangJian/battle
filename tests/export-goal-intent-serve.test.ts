import { describe, expect, it } from 'bun:test'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { join, relative } from 'node:path'
import { main as goalMain } from '../tools/sim/export-goal-rollout'
import { main as intentMain } from '../tools/sim/export-intent-rollout'
import { unpackContainer } from '../tools/sim/pack-container'

/**
 * `export-goal-rollout.ts --serve` / `export-intent-rollout.ts --serve`：让 goal/intent 两个
 * 导出器进 agent 的 persist 池（plan `src/nn/conv/conv-optimize.plan.md` §4.6 Stage 4）。
 *
 * 为什么：agent（sampler-agent.ts）原先只给 `export-rl-rollout` / `export-eval-game` 开了长驻
 * worker，goal/intent 每局都 `spawn` 一个新 bun —— 这两个模式**局数多、单局短**，spawn 成本占比
 * 最高，正是长驻收益最大的地方（计划预估 +15–19%）。
 *
 * 判据（每个导出器只起**一个**子进程，基线在进程内跑）：① READY/OK 握手；② serve 与一次性调用
 * 的产物**逐字节一致**（shard 树 + 容器内的 shard 条目；manifest 只除墙钟 `elapsedSec`）；
 * ③ 坏行响亮报错且 worker 继续服务；④ 同 worker 换 seed 的第二局按本局成包（不串局、worker 仍活）。
 * 协议本身收在 `tools/sim/serve-loop.ts`（唯一实现，四个导出器共用）。
 */
const REPO_ROOT = join(import.meta.dir, '..')
const MAX_TICKS = '120' // 瘦身局：本测只钉接线与等价性，不测玩法

interface Exporter {
  name: string
  entry: string
  fixture: string
  kind: string
  main: (argv: string[]) => void
}

const EXPORTERS: Exporter[] = [
  {
    name: 'goal',
    entry: 'tools/sim/export-goal-rollout.ts',
    fixture: 'goal-golden.json',
    kind: 'goal',
    main: goalMain,
  },
  {
    name: 'intent',
    entry: 'tools/sim/export-intent-rollout.ts',
    fixture: 'intent-golden.json',
    kind: 'intent',
    main: intentMain,
  },
]

/** h16/d2 瘦身权重（与训练产物解耦，见 tests/nn/intent-rl-rollout.test.ts 同口径）。 */
function slimWeights(dir: string, e: Exporter): string {
  const g = JSON.parse(readFileSync(join(REPO_ROOT, 'tests', 'fixtures', e.fixture), 'utf8')) as {
    h: number
    d: number
    params: Record<string, unknown>
  }
  const p = join(dir, 'weights.json')
  writeFileSync(p, JSON.stringify({ arch: { kind: e.kind, h: g.h, d: g.d }, params: g.params }))
  return p
}

/** 一次性调用的 argv（含入口路径；serve 送的是 slice(1)）。 */
function taskArgs(e: Exporter, weights: string, out: string, pack: string, seed: number): string[] {
  return [
    e.entry,
    '--weights',
    weights,
    '--out',
    out,
    '--difficulty',
    'hard',
    '--stages',
    '0',
    '--seeds',
    String(seed),
    '--max-ticks',
    MAX_TICKS,
    '--wver',
    'serve-test',
    '--node-label',
    'serve-test',
    '--pack',
    pack,
  ]
}

/** 目录树快照（跳过包文件与汇总报告：前者自带墙钟，后者由 pack 判据覆盖）。 */
function snapshotTree(dir: string): Record<string, string> {
  const out: Record<string, string> = {}
  const walk = (d: string): void => {
    for (const name of readdirSync(d)) {
      const p = join(d, name)
      if (statSync(p).isDirectory()) walk(p)
      else if (!name.endsWith('.pack') && name !== '_rl_report.json')
        out[relative(dir, p).replace(/\\/g, '/')] = readFileSync(p).toString('base64')
    }
  }
  walk(dir)
  return out
}

/** 容器判据：shard 条目逐字节相同，manifest 只除墙钟 elapsedSec。 */
function expectSamePack(a: string, b: string): void {
  const pa = unpackContainer(readFileSync(a))
  const pb = unpackContainer(readFileSync(b))
  const strip = (m: Record<string, unknown>): Record<string, unknown> => {
    const { elapsedSec: _drop, ...rest } = m
    return rest
  }
  expect(strip(pb.manifest)).toEqual(strip(pa.manifest))
  expect([...pb.entries.keys()].sort()).toEqual([...pa.entries.keys()].sort())
  expect(pb.entries.size).toBeGreaterThan(0) // 真的写了 shard（不是空容器）
  for (const [name, bytes] of pa.entries) {
    expect(Buffer.from(pb.entries.get(name)!).equals(Buffer.from(bytes))).toBe(true)
  }
}

/** 起一个 serve worker，返回送任务/读标记的工具（agent 侧只认 `__SERVE_*__` 行）。 */
async function startServe(e: Exporter): Promise<{
  send: (argv: string[]) => void
  sendRaw: (line: string) => void
  nextMarker: () => Promise<string>
  waitOk: () => Promise<void>
  alive: () => boolean
  stop: () => void
}> {
  const proc = Bun.spawn([process.execPath, e.entry, '--serve'], {
    cwd: REPO_ROOT,
    stdin: 'pipe',
    stdout: 'pipe',
    stderr: 'pipe',
  })
  const reader = proc.stdout.getReader()
  const decoder = new TextDecoder()
  const seen: string[] = []
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
  const nextMarker = async (): Promise<string> => {
    for (;;) {
      const line = await nextLine()
      if (line.startsWith('__SERVE_')) return line
    }
  }
  return {
    send: (argv) => proc.stdin.write(`${JSON.stringify(argv)}\n`),
    sendRaw: (line) => proc.stdin.write(`${line}\n`),
    nextMarker,
    waitOk: async () => {
      const line = await nextMarker()
      if (line !== '__SERVE_OK__') throw new Error(`serve 未报 OK：${line}`)
    },
    alive: () => proc.exitCode === null,
    stop: () => proc.kill('SIGTERM'),
  }
}

describe('goal/intent rollout --serve（persist 池）', () => {
  for (const e of EXPORTERS) {
    it(`${e.name}：握手 + 与一次性调用逐字节等价 + 坏行不致命 + 跨局不串`, async () => {
      const fixturePath = join(REPO_ROOT, 'tests', 'fixtures', e.fixture)
      if (!existsSync(fixturePath)) throw new Error(`missing ${relative(REPO_ROOT, fixturePath)}`)
      const base = join(REPO_ROOT, 'tmp')
      mkdirSync(base, { recursive: true })
      const work = mkdtempSync(join(base, `${e.name}-serve-`))
      const weights = slimWeights(work, e)

      // ---- 基线：进程内直接调 main（省一次 bun 冷启动）----
      const oneShotOut = join(work, 'oneshot')
      const oneShotPack = join(work, 'oneshot.pack')
      e.main(taskArgs(e, weights, oneShotOut, oneShotPack, 11).slice(1))
      const expectedTree = snapshotTree(oneShotOut)

      const worker = await startServe(e)
      try {
        expect(await worker.nextMarker()).toBe('__SERVE_READY__')

        // ① 与一次性调用逐字节等价（shard 树 + 容器条目）
        const outA = join(work, 'a')
        const packA = join(work, 'a.pack')
        worker.send(taskArgs(e, weights, outA, packA, 11).slice(1))
        await worker.waitOk()
        expect(snapshotTree(outA)).toEqual(expectedTree)
        expectSamePack(oneShotPack, packA)

        // ② 坏行：响亮报错，worker 不倒。三类都算坏行：非法 JSON、合法但非数组的 JSON
        // （`123`/`{}` 放进去会让 main 拿不到 argv 而静默跑默认网格）、以及空数组之外的杂物。
        for (const bad of ['{not json', '123', '{}']) {
          worker.sendRaw(bad)
          expect(await worker.nextMarker()).toBe('__SERVE_ERR__ bad-json')
        }
        expect(worker.alive()).toBe(true)

        // ③ 换 seed 的第二局：按本局成包、worker 仍活着
        const outB = join(work, 'b')
        const packB = join(work, 'b.pack')
        worker.send(taskArgs(e, weights, outB, packB, 12).slice(1))
        await worker.waitOk()
        expect(unpackContainer(readFileSync(packB)).manifest.seed).toBe(12)
        expect(unpackContainer(readFileSync(packA)).manifest.seed).toBe(11)
        expect(worker.alive()).toBe(true)
      } finally {
        worker.stop()
        rmSync(work, { recursive: true, force: true })
      }
    }, 60_000)
  }
})

describe('PERSIST_SERVE_ENTRIES 与 --serve 实现同规', () => {
  it('池里每个条目都真的走 runServe（否则一进池就是「跑完默认网格后退出」）', () => {
    const src = readFileSync(join(REPO_ROOT, 'tools', 'agent', 'sampler-agent.ts'), 'utf8')
    const head = src.indexOf('export const PERSIST_SERVE_ENTRIES')
    expect(head).toBeGreaterThan(0)
    const block = src.slice(head, src.indexOf('])', head))
    const entries = [...block.matchAll(/'([^']*\.ts)'/g)].map((m) => m[1]!)
    expect(entries.length).toBeGreaterThanOrEqual(4) // rl / eval / goal / intent
    for (const e of entries) {
      const f = join(REPO_ROOT, e)
      expect(existsSync(f)).toBe(true)
      expect(readFileSync(f, 'utf8').includes('runServe(main)')).toBe(true)
    }
  })
})
