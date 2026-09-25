import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { cloneWorld } from '../src/snapshot/WorldSerializer'
import { worldTickHash } from '../src/replay/tickHash'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import type { Direction } from '../src/constants'
import type { InputLike } from '../src/game/Input'
import {
  METRICS_DIM,
  METRICS_VERSION,
  applyInitSnapshot,
  loadInitSnapshot,
  runOneBench,
} from '../tools/sim/export-rl-rollout'
import { hashIndexFor, materializeCuts } from '../tools/sim/build-state-init-bank'

/**
 * 起始分布注入（plan/x20-state-init.plan.md P0/P1）的护栏。
 *
 * 为什么这组用例值得存在：`--init-snapshot` 的失效模式**全是静默的**——
 *   ① 快照没被用上（老实现忽略未知 flag）⇒ 跑的是标准开局，日志/账本却说中段起跑
 *      （= 换了一个实验，而判据照算）；
 *   ② 交棒点不在决策边界上 ⇒ 首个决策前有 K−1 tick 由残留动作驱动；
 *   ③ restore 之后忘了重开 RNG ⇒ 不同 seed 产出内容完全相同的局（旋转种子名不副实）；
 *   ④ tickHash 与快照不匹配（银行/导出器版本不一致）⇒ 起始状态根本不是人类到达过的那个。
 * 四条都在这里钉死：每条断言对应一种「看起来跑起来了」的错。
 *
 * 用例全程确定性：固定 seed、无 `Math.random()`、无墙钟（§2.3）。
 */

/** 最小 InputLike：站着开火（夹具世界用；决定论且不易提前死亡）。 */
class FixtureInput implements InputLike {
  getMoveDirection(): Direction | null {
    return null
  }
  isFiring(): boolean {
    return true
  }
  wasItemPressed(): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

const CUT = 300

/** 造一个「中段世界」快照文件（真跑仿真 → cloneWorld → 包 wrapper），返回其 hash。 */
function writeSnapshot(dir: string, over: { tickHash?: string; patch?: (s: any) => void } = {}) {
  const world = new World()
  world.rng.reseed(4242)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic']
  world.playerLevel = 0
  world.lives = 3
  world.loadStageData(STAGES[0], 0)
  const sim = new Simulation(world, new FixtureInput())
  for (let i = 0; i < CUT; i++) sim.tick()
  expect(world.state).toBe('playing')
  expect(world.player?.alive).toBe(true)
  const snap: any = cloneWorld(world)
  if (over.patch) over.patch(snap)
  const path = join(dir, `s0-4242-t${CUT}.json`)
  writeFileSync(
    path,
    JSON.stringify({
      version: 1,
      id: 's0-4242',
      stage: 0,
      demoSeed: 4242,
      tick: CUT,
      tickHash: over.tickHash ?? worldTickHash(world),
      human: { kills: world.killCount },
      snapshot: snap,
    }),
  )
  return { path, tickHash: worldTickHash(world), kills: world.killCount, snap }
}

function tmp(): string {
  return mkdtempSync(join(tmpdir(), 'state-init-'))
}

/**
 * 一份零填充的最小 student 权重（只为把 `runOne` 跑起来）。
 *
 * 为什么不读真权重：RL 权重 JSON 落在 `nn-training/weights/**`（gitignore，不入库）——用例
 * 依赖它就会在干净的 clone 上红。arch/形状与真权重同源（in_ch=16, board=26, h=64）；
 * 形状错了 `StudentModel` 会当场报维度错，所以这份表也是「schema 变了就红」的钉子。
 */
function zeroStudentWeights(): string {
  const shapes: Record<string, number[]> = {
    'stem.weight': [64, 18, 3, 3],
    'stem.bias': [64],
    'fc.weight': [128, 94],
    'fc.bias': [128],
    'move_head.weight': [5, 128],
    'move_head.bias': [5],
    'fire_head.weight': [2, 128],
    'fire_head.bias': [2],
    'value_head.weight': [1, 128],
    'value_head.bias': [1],
  }
  for (let i = 0; i < 8; i++) {
    shapes[`blocks.${i}.dw.weight`] = [64, 1, 5, 5]
    shapes[`blocks.${i}.dw.bias`] = [64]
    shapes[`blocks.${i}.pw.weight`] = [64, 64, 1, 1]
    shapes[`blocks.${i}.pw.bias`] = [64]
  }
  const params: Record<string, { shape: number[]; data: string }> = {}
  for (const [name, shape] of Object.entries(shapes)) {
    const n = shape.reduce((a, b) => a * b, 1)
    const f32 = new Float32Array(n)
    params[name] = { shape, data: Buffer.from(f32.buffer).toString('base64') }
  }
  return JSON.stringify({
    format: 'nn-weights-json',
    version: 1,
    schema_major: 3,
    arch: { kind: 'student', in_ch: 16, board: 26, scalar_dim: 30, h: 64, d: 8, head_hidden: 128 },
    params,
  })
}

const WEIGHTS = zeroStudentWeights()

describe('P0 切点物化（纯函数）', () => {
  it('按 [cut_from, cut_to] 步 cut_step 物化，上界对局尾回退', () => {
    const cuts = materializeCuts(5260, { cut_from: 300, cut_to: -120, cut_step: 300 })
    expect(cuts[0]).toBe(300)
    expect(cuts[cuts.length - 1]).toBe(5100) // 5260-120 = 5140 → 最后一个 ≤5140 的 300 倍数
    expect(cuts.length).toBe(17)
    expect(cuts.every((t) => t % 300 === 0)).toBe(true)
  })

  it('空集是空集（不钳到最小合法切点）', () => {
    expect(materializeCuts(200, { cut_from: 300, cut_to: -120, cut_step: 300 })).toEqual([])
  })

  it('不对齐决策边界 / tickHash 间隔 ⇒ 响亮拒（门的前提，不是风格）', () => {
    expect(() => materializeCuts(5000, { cut_from: 305, cut_to: -120, cut_step: 300 })).toThrow(
      /决策边界/,
    )
    expect(() => materializeCuts(5000, { cut_from: 350, cut_to: -120, cut_step: 300 })).toThrow(
      /tickHash/,
    )
    expect(() => materializeCuts(5000, { cut_from: -300, cut_to: -120, cut_step: 300 })).toThrow(
      /不得为负/,
    )
    expect(() => materializeCuts(5000, { cut_from: 300, cut_to: 120, cut_step: 300 })).toThrow(/≤0/)
    expect(() => materializeCuts(5000, { cut_from: 300, cut_to: -120, cut_step: 0 })).toThrow(/≥1/)
  })

  it('hashIndexFor：只有 hole 上才有点（保真门的对齐前提）', () => {
    expect(hashIndexFor(600, 100)).toBe(6)
    expect(hashIndexFor(650, 100)).toBe(0)
    expect(hashIndexFor(0, 100)).toBe(0)
    expect(hashIndexFor(600, 0)).toBe(0)
  })
})

describe('P1 快照读取校验（loadInitSnapshot）', () => {
  it('缺文件 / 缺 tickHash / 缺 snapshot / 跨关 / 不对齐 / 越界 —— 全部响亮拒', () => {
    const dir = tmp()
    const { path } = writeSnapshot(dir)
    const opts = { stageIdx: 0, maxTicks: 900 }
    expect(loadInitSnapshot(path, opts).tick).toBe(CUT)

    expect(() => loadInitSnapshot(join(dir, 'nope.json'), opts)).toThrow(/不存在/)

    const noHash = join(dir, 'no-hash.json')
    writeFileSync(noHash, JSON.stringify({ stage: 0, tick: CUT, snapshot: {} }))
    expect(() => loadInitSnapshot(noHash, opts)).toThrow(/缺 tickHash/)

    const noSnap = join(dir, 'no-snap.json')
    writeFileSync(noSnap, JSON.stringify({ stage: 0, tick: CUT, tickHash: 'x' }))
    expect(() => loadInitSnapshot(noSnap, opts)).toThrow(/缺 snapshot/)

    // 跨关借状态是另一个实验（plan §2 B1）
    expect(() => loadInitSnapshot(path, { stageIdx: 2000, maxTicks: 900 })).toThrow(/跨关借状态/)

    const offGrid = join(dir, 'off-grid.json')
    writeFileSync(offGrid, JSON.stringify({ stage: 0, tick: CUT + 5, tickHash: 'x', snapshot: {} }))
    expect(() => loadInitSnapshot(offGrid, { stageIdx: 0, maxTicks: 900 })).toThrow(/决策边界/)

    expect(() => loadInitSnapshot(path, { stageIdx: 0, maxTicks: CUT })).toThrow(/maxTicks/)
  })
})

describe('P1 注入语义（applyInitSnapshot / runOneBench 端到端）', () => {
  it('restore 得到人类世界；RNG 用本局 seed 重开；CLI 权威值覆盖快照', () => {
    const dir = tmp()
    const { path, tickHash } = writeSnapshot(dir)
    const snap = loadInitSnapshot(path, { stageIdx: 0, maxTicks: 900 })
    const world = new World()
    world.rng.reseed(1)
    world.loadStageData(STAGES[0], 0)

    applyInitSnapshot(world, snap, {
      seed: 4242,
      difficultyKey: 'classic',
      livesOverride: 1,
      playerLevelOverride: null,
    })
    expect(world.frame).toBe(CUT)
    expect(world.state).toBe('playing')
    expect(world.player?.alive).toBe(true)
    expect(world.lives).toBe(1) // CLI 权威（restore 会带回快照里的 3）
    expect(world.rng.getState()).toBe(4242) // B1：状态来自人类，未来由本局 seed 决定
    // 重开 RNG 之后 hash 必然不再等于录像那一点（正因为如此，hash 校验必须在 reseed **之前**）
    expect(worldTickHash(world)).not.toBe(tickHash)
  })

  it('端到端：交棒行的 tick == initTick、计数器起点 = 继承事实、manifest 三字段齐', () => {
    const dir = tmp()
    const { path, kills } = writeSnapshot(dir)
    const res = runOneBench(0, STAGES[0], 4242, 'classic', 900, WEIGHTS, 'off', false, 1, null, {
      path,
    })
    expect(res.initTick).toBe(CUT)
    expect(res.initSnapshot).toBe(basename(path))
    expect(res.initCounters).not.toBeNull()
    expect(res.initCounters!.kills).toBe(kills)
    expect(res.initCounters!.frame).toBe(CUT)
    expect(res.shard.n).toBeGreaterThan(0)
    const m = res.shard.metrics
    expect(m.length).toBe(res.shard.n + 1) // N 个决策快照 + 1 个终局快照
    expect(m[0].length).toBe(METRICS_DIM)
    // metrics 行首列 = ticks：交棒行必须是**游戏时钟**（cut 吃掉 T），不是 0
    expect(m[0][0]).toBe(CUT)
    expect(m[m.length - 1][0]).toBeGreaterThanOrEqual(CUT)
    expect(m[m.length - 1][0]).toBeLessThanOrEqual(900)
  })

  it('快照与 tickHash 不匹配（被改过/版本不一致）⇒ 该局响亮失败，不静默跑标准开局', () => {
    const dir = tmp()
    const { path } = writeSnapshot(dir, { patch: (s) => void (s.lives = 2) })
    expect(() =>
      runOneBench(0, STAGES[0], 4242, 'classic', 900, WEIGHTS, 'off', false, 1, null, { path }),
    ).toThrow(/tickHash 自检失败/)
  })

  it('不注入时逐字段旧行为（tick 从 0 起、无 init 字段、确定论可复现）', () => {
    const a = runOneBench(0, STAGES[0], 4242, 'classic', 600, WEIGHTS, 'off', false, 1, null, null)
    const b = runOneBench(0, STAGES[0], 4242, 'classic', 600, WEIGHTS, 'off', false, 1, null, null)
    expect(a.initTick).toBe(0)
    expect(a.initCounters).toBeNull()
    expect(a.initSnapshot).toBe('')
    expect(a.shard.metrics[0][0]).toBe(0)
    expect(METRICS_VERSION).toBe(8) // 本 plan 不动 metrics 版本/列宽
    expect(a.shard.n).toBe(b.shard.n)
    expect(a.ticks).toBe(b.ticks)
    expect(a.kills).toBe(b.kills)
    expect(a.shard.metrics.slice(0, 3)).toEqual(b.shard.metrics.slice(0, 3))
  })
})

const REPO_ROOT = join(import.meta.dir, '..')

/**
 * P1b：**长驻池（`--serve`）与一次性调用逐字节一致** —— 含起始分布注入。
 *
 * 为什么值得（2026-09-25）：本机腿（`rl/queue_local.py`）原先每局 `Popen` 一个 bun，现在默认
 * 交给 `--serve` 长驻池（节点侧同款池实测 1.59×，见 `docs/nn/runtime-opt.md` §20/§21）。池化的
 * 硬前提是「一个任务一局、每局新建 World」逐字节等价（`tools/sim/serve-loop.ts` 的 docstring），
 * 而起始分布是这条前提上最容易被突破的一处：restore 会把**上一局残留的世界**换成快照，
 * 只要池里留了任何跨局状态（缓存世界/复用 rng/忘了重建 World），产物就会悄悄不等于一次性调用
 * ——那时账本上写的是「中段起跑」，而样本来自别的世界。
 *
 * 判据：同一个 argv（同一份快照文件、同一 seed）跑两遍——A 各开一个子进程（一次性），
 * B 同一个 serve 进程连跑两局——逐文件对账，并两边都断言 `initTick` 落地。
 */
describe('P1b 长驻池（--serve）≡ 一次性调用（含起始分布注入）', () => {
  it('同一 argv：池连跑两局 vs 各自一次性调用，逐文件相同且都带 initTick', async () => {
    const dir = tmp()
    const { path: snapPath } = writeSnapshot(dir)
    const wpath = join(dir, 'w.json')
    writeFileSync(wpath, WEIGHTS)
    /** 与 `build_rollout_cmd` 产出的 argv 同形（含起始分布与课程权威值）。 */
    const argv = (out: string, seed: number): string[] => [
      'tools/sim/export-rl-rollout.ts',
      '--weights',
      wpath,
      '--out',
      out,
      '--stages',
      '0',
      '--seeds',
      String(seed),
      '--max-ticks',
      '400',
      '--difficulty',
      'classic',
      '--wver',
      'serve-test',
      '--node-label',
      'serve-test',
      '--lives-override',
      '1',
      '--init-snapshot',
      snapPath,
    ]
    const SEEDS = [42, 43]
    for (const seed of SEEDS) {
      const p = Bun.spawnSync([process.execPath, ...argv(join(dir, `oneshot-${seed}`), seed)], {
        cwd: REPO_ROOT,
        stdout: 'pipe',
        stderr: 'pipe',
      })
      expect(p.exitCode).toBe(0)
    }

    const proc = Bun.spawn([process.execPath, 'tools/sim/export-rl-rollout.ts', '--serve'], {
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
      /** 只认 `__SERVE_*__` 标记行（main 自己的汇总日志混在同一路 stdout —— agent 侧同规）。 */
      const nextMarker = async (): Promise<string> => {
        for (;;) {
          const line = await nextLine()
          if (line.startsWith('__SERVE_')) return line
        }
      }
      expect(await nextMarker()).toBe('__SERVE_READY__')
      for (const seed of SEEDS) {
        proc.stdin.write(JSON.stringify(argv(join(dir, `pooled-${seed}`), seed).slice(1)) + '\n')
        const marker = await nextMarker()
        if (marker !== '__SERVE_OK__') throw new Error(`serve 未报 OK：${marker}`)
      }
      expect(proc.exitCode).toBe(null) // 两局都在同一个 worker 里跑完
    } finally {
      proc.kill('SIGTERM')
    }

    for (const seed of SEEDS) {
      const a = join(dir, `oneshot-${seed}`, `rl_s0_seed${seed}`)
      const b = join(dir, `pooled-${seed}`, `rl_s0_seed${seed}`)
      const files = readdirSync(a).sort()
      expect(files.length).toBeGreaterThan(0)
      expect(readdirSync(b).sort()).toEqual(files)
      for (const f of files) {
        expect(readFileSync(join(b, f))).toEqual(readFileSync(join(a, f)))
      }
      const man = JSON.parse(readFileSync(join(b, 'manifest.json'), 'utf8')) as Record<
        string,
        unknown
      >
      expect(man.initTick).toBe(CUT)
      expect(man.initSnapshot).toBe(basename(snapPath))
    }
  }, 60_000)
})
