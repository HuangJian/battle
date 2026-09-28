import { describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  PERSIST_MODE_BY_ENTRY,
  PERSIST_SERVE_ENTRY,
  persistModeFor,
} from '../tools/agent/persist-pool'
import { main as evalMain } from '../tools/sim/export-eval-game'
import { main as goalMain } from '../tools/sim/export-goal-rollout'
import { unpackContainer } from '../tools/sim/pack-container'
import { SERVE_MODES } from '../tools/sim/serve-any'

/**
 * `tools/sim/serve-any.ts` —— 长驻池的**同质入口**（2026-09-28，DECISIONS
 * §2026-09-28-goalnn-persist-homogeneous-serve）。
 *
 * 为什么（a95 真机归因，docs/nn/runtime-opt.md §27）：池原先按导出器分「腿」，而 worker 的入口
 * 在 spawn 时烧死在 argv[0] ⇒ 预热只能猜腿（按盘上权重 mtime），猜错腿的那一批每一局都退回一次性
 * spawn（实测 3.1s/局 vs 对腿 1.55s/局），换腿还要退役空闲 worker 再补满（一次 ~39s）。同质化把
 * 「选哪个导出器」从 **spawn 时的 argv** 挪到 **每行的 mode token**：池里没有腿，猜与换腿都消失。
 *
 * 判据：
 *   ① 两张表同集（`PERSIST_MODE_BY_ENTRY` 的值域 == `SERVE_MODES` 的键集）——任何一侧加导出器而
 *      另一侧没跟上都要红；
 *   ② **同一个进程交替跑两种 mode**，每局产物与一次性调用**逐字节一致**（这条是同质化的代价所在：
 *      分派错了不再有进程边界挡着，只能靠这条钉子 + 未知 mode 响亮报错）；
 *   ③ 未知 mode ⇒ `__SERVE_ERR__`（不静默跑默认网格），且 worker 之后照常服务。
 */
const REPO_ROOT = join(import.meta.dir, '..')
const MAX_TICKS = '120' // 瘦身局：本测只钉分派与等价性，不测玩法

interface Task {
  /** 该导出器的一次性 argv（**不含**入口路径）。 */
  flags: (dir: string, pack: string, seed: number) => string[]
}

/** 瘦身权重（与训练产物解耦，同 tests/export-*-serve.test.ts 口径）。 */
function weightsFile(dir: string, name: string, fixture: string, kind: string): string {
  const g = JSON.parse(readFileSync(join(REPO_ROOT, 'tests', 'fixtures', fixture), 'utf8')) as {
    h: number
    d: number
    params: Record<string, unknown>
  }
  const w = join(dir, `${name}.json`)
  writeFileSync(w, JSON.stringify({ arch: { kind, h: g.h, d: g.d }, params: g.params }))
  return w
}

const evalTask = (weights: string): Task => ({
  flags: (dir, pack, seed) => [
    '--weights',
    weights,
    '--out',
    dir,
    '--stage',
    '0',
    '--seed',
    String(seed),
    '--max-ticks',
    MAX_TICKS,
    '--difficulty',
    'hard',
    '--wver',
    'serve-any-test',
    '--node-label',
    'serve-any-test',
    '--lives-override',
    '1',
    '--pack',
    pack,
  ],
})

const goalTask = (weights: string): Task => ({
  flags: (dir, pack, seed) => [
    '--weights',
    weights,
    '--out',
    dir,
    '--difficulty',
    'hard',
    '--stages',
    '0',
    '--seeds',
    String(seed),
    '--max-ticks',
    MAX_TICKS,
    '--wver',
    'serve-any-test',
    '--node-label',
    'serve-any-test',
    '--pack',
    pack,
  ],
})

/** 除墙钟 elapsedSec 外逐字段相同；有 shard 的（goal/rollout）还要逐字节相同。 */
function expectSamePack(expected: string, actual: string): number {
  const pa = unpackContainer(readFileSync(expected))
  const pb = unpackContainer(readFileSync(actual))
  const strip = (m: Record<string, unknown>): Record<string, unknown> => {
    const { elapsedSec: _drop, ...rest } = m
    return rest
  }
  expect(strip(pb.manifest)).toEqual(strip(pa.manifest))
  expect([...pb.entries.keys()].sort()).toEqual([...pa.entries.keys()].sort())
  for (const [name, bytes] of pa.entries) {
    expect(Buffer.from(pb.entries.get(name)!).equals(Buffer.from(bytes))).toBe(true)
  }
  return pa.entries.size
}

describe('serve-any mode 表与 agent 侧同集', () => {
  it('PERSIST_MODE_BY_ENTRY 的值域 == SERVE_MODES 的键集（两侧任何一边加导出器都要红）', () => {
    expect(Object.values(PERSIST_MODE_BY_ENTRY).sort()).toEqual(Object.keys(SERVE_MODES).sort())
    // 表里的条目必须真的存在（改名 / 搬文件时在这里红，而不是在上机时）
    for (const entry of Object.keys(PERSIST_MODE_BY_ENTRY)) {
      expect(existsSync(join(REPO_ROOT, entry))).toBe(true)
    }
    expect(PERSIST_SERVE_ENTRY).toBe('tools/sim/serve-any.ts')
    expect(existsSync(join(REPO_ROOT, PERSIST_SERVE_ENTRY))).toBe(true)
  })

  it('Python 侧镜像同源：`nn-training/common/manifest.py` 的 SERVE_MODE_BY_SCRIPT 逐条对得上', () => {
    // 这张表现在活在两种语言里（TS agent 池 / Python 节点与本机池），值域就是 `serve-any.ts`
    // 的 mode 键集 —— 一侧改名而另一侧没跟上，worker 会当场报 `unknown mode`（响亮，但等到
    // 上机才知道）。这条钉子把它拉到改代码的那一刻：
    //   * TS 侧 `PERSIST_MODE_BY_ENTRY` / `SERVE_MODES` 已在上面互钉；
    //   * Python 侧声明行必须逐字覆盖这四个 (脚本, token) 对。
    const py = readFileSync(join(REPO_ROOT, 'nn-training', 'common', 'manifest.py'), 'utf8')
    const constFor = (script: string): string | null => {
      const esc = script.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return new RegExp(`^([A-Z_]+) = "${esc}"$`, 'm').exec(py)?.[1] ?? null
    }
    for (const [script, mode] of Object.entries(PERSIST_MODE_BY_ENTRY)) {
      const c = constFor(script)
      expect(c, `manifest.py 里没有指向 ${script} 的常量定义`).not.toBeNull()
      expect(py.includes(`    ${c}: "${mode}",`), `${c} 的 token 不等于 ${mode}`).toBe(true)
    }
    expect(py.includes('SERVE_ANY_SCRIPT = "tools/sim/serve-any.ts"')).toBe(true)
    expect(py.includes('def serve_mode_for(')).toBe(true)
  })

  it('persistModeFor：表内条目给 token，表外（BC / 未知）给 null ⇒ 调用方走一次性 spawn', () => {
    expect(persistModeFor('tools/sim/export-rl-rollout.ts')).toBe('rollout')
    expect(persistModeFor('tools/sim/export-eval-game.ts')).toBe('eval')
    expect(persistModeFor('tools/sim/export-goal-rollout.ts')).toBe('goal')
    expect(persistModeFor('tools/sim/export-intent-rollout.ts')).toBe('intent')
    // BC 是教师口径的独立出口（不进池）：null，而不是一个 token
    expect(persistModeFor('tools/sim/export-godai-bc.ts')).toBeNull()
    expect(persistModeFor('tools/sim/nope.ts')).toBeNull()
  })
})

describe('serve-any --serve（同进程交替跑两种 mode）', () => {
  it('eval → goal → 未知 mode → eval：每局与一次性等价，坏 mode 不致命', async () => {
    for (const f of ['student-golden.json', 'goal-golden.json']) {
      if (!existsSync(join(REPO_ROOT, 'tests', 'fixtures', f)))
        throw new Error(`missing tests/fixtures/${f}`)
    }
    const base = join(REPO_ROOT, 'tmp')
    mkdirSync(base, { recursive: true })
    const work = mkdtempSync(join(base, 'serve-any-'))
    const ev = evalTask(weightsFile(work, 'eval-w', 'student-golden.json', 'student'))
    const go = goalTask(weightsFile(work, 'goal-w', 'goal-golden.json', 'goal'))

    // ---- 基线：进程内直接调 main（省两次冷启动；那也是「一次性路径」的同一入口）----
    const evOne = join(work, 'eval-one')
    const evOnePack = join(work, 'eval-one.pack')
    evalMain(ev.flags(evOne, evOnePack, 11))
    expect(unpackContainer(readFileSync(evOnePack)).manifest.mode).toBe('eval')
    const goOne = join(work, 'goal-one')
    const goOnePack = join(work, 'goal-one.pack')
    goalMain(go.flags(goOne, goOnePack, 11))

    const proc = Bun.spawn([process.execPath, PERSIST_SERVE_ENTRY, '--serve'], {
      cwd: REPO_ROOT,
      stdin: 'pipe',
      stdout: 'pipe',
      stderr: 'pipe',
    })
    const reader = proc.stdout.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    const nextLine = async (): Promise<string> => {
      for (;;) {
        const nl = buf.indexOf('\n')
        if (nl >= 0) {
          const l = buf.slice(0, nl).trim()
          buf = buf.slice(nl + 1)
          if (l) return l
          continue
        }
        const { value, done } = await reader.read()
        if (done) throw new Error('serve-any stdout 关闭')
        buf += decoder.decode(value as Uint8Array)
      }
    }
    const nextMarker = async (): Promise<string> => {
      for (;;) {
        const l = await nextLine()
        if (l.startsWith('__SERVE_')) return l
      }
    }
    const send = (mode: string, argv: string[]): void => {
      proc.stdin.write(`${JSON.stringify([mode, ...argv])}\n`)
    }
    try {
      expect(await nextMarker()).toBe('__SERVE_READY__')

      // ① mode=eval 一局
      const evA = join(work, 'eval-serve-a')
      const evAPack = join(work, 'eval-serve-a.pack')
      send('eval', ev.flags(evA, evAPack, 11))
      expect(await nextMarker()).toBe('__SERVE_OK__')
      expectSamePack(evOnePack, evAPack)

      // ② **同一个进程**接着跑 mode=goal（「池里没有腿」的直接证据）
      const goA = join(work, 'goal-serve-a')
      const goAPack = join(work, 'goal-serve-a.pack')
      send('goal', go.flags(goA, goAPack, 11))
      expect(await nextMarker()).toBe('__SERVE_OK__')
      expect(expectSamePack(goOnePack, goAPack)).toBeGreaterThan(0) // 真写了 shard，不是空容器

      // ③ 未知 mode：响亮报错（不静默跑默认网格），worker 之后照常服务
      send('nope', ['--weights', 'x'])
      expect(await nextMarker()).toStartWith('__SERVE_ERR__')
      expect(proc.exitCode).toBeNull() // 没被打死

      // ④ 回到 mode=eval，换 seed（不串局）
      const evB = join(work, 'eval-serve-b')
      const evBPack = join(work, 'eval-serve-b.pack')
      send('eval', ev.flags(evB, evBPack, 12))
      expect(await nextMarker()).toBe('__SERVE_OK__')
      const evOneB = join(work, 'eval-one-b')
      const evOneBPack = join(work, 'eval-one-b.pack')
      evalMain(ev.flags(evOneB, evOneBPack, 12))
      expectSamePack(evOneBPack, evBPack)
    } finally {
      try {
        proc.kill()
      } catch {
        /* gone */
      }
      rmSync(work, { recursive: true, force: true })
    }
  }, 60_000)
})
