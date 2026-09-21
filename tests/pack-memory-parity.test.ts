/**
 * pack-memory-parity.test.ts —— `--pack-memory` 与「写盘 shard」两条路径的**字节等价**门禁
 * （plan/rollout-eval-opt.plan.md §3 遗留项，2026-09-21）。
 *
 * 为什么要有这个门禁：`--pack-memory` 是 sampler 一路的默认（省掉每局 7.5ms 写盘 + 7ms 读回），
 * 它的正确性此前只靠一次**手工冒烟**（`tmp/pack-a.pack` vs `tmp/pack-b.pack`，5561B）。
 * 而这两条路径在代码里是**两套组装**：内存路径走 `shardNpyEntries(shard)`，写盘路径走
 * `RL_SHARD_FILES.map(readFileSync)` —— 只要有人给 shard 加一个字段而忘了同步
 * `shardNpyEntries`，内存路径就会静默少一个文件、训练侧读到残缺语料（同族事故：
 * obs-encoder 升 v3 时 `writeRlShard` 忘记同步行宽 ⇒ 整条腿零产出）。
 *
 * 三层断言，越靠后越接近生产：
 *   ① 函数级：`shardNpyEntries` 与 `writeRlShard` 落盘的文件**逐字节**相等、名字集合/顺序一致；
 *   ② 组装级：`buildPack` 对两份 entries 产出的 pack **逐字节**相等；
 *   ③ 端到端：真跑两次 exporter（同权重/关卡/种子），比对 `_result.pack` 字节，
 *      并验「内存模式不写 npy 目录、写盘模式写」这一行为差异。
 */
import { describe, expect, it } from 'bun:test'
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  METRICS_DIM,
  MASK_DIM,
  RL_SHARD_FILES,
  shardNpyEntries,
  writeRlShard,
  type ShardData,
} from '../tools/sim/export-rl-rollout'
import { buildPack, unpackContainer } from '../tools/sim/pack-container'
import { OBS_CHANNELS, BOARD, SCALAR_DIM } from '../src/nn/obs-encoder'

/** 确定性 shard（内容与形状都覆盖：obs 每行首字节带行号，便于错位暴露）。 */
function makeShard(n = 5): ShardData {
  const obs: Uint8Array[] = []
  const scalars: Float32Array[] = []
  const metrics: number[][] = []
  for (let i = 0; i < n; i++) {
    const o = new Uint8Array(OBS_CHANNELS * BOARD * BOARD)
    o.fill((i * 37 + 11) % 251)
    o[0] = i + 1
    obs.push(o)
    const s = new Float32Array(SCALAR_DIM)
    s.fill(i + 0.5)
    scalars.push(s)
    metrics.push(Array.from({ length: METRICS_DIM }, (_, k) => i + k))
  }
  metrics.push(Array.from({ length: METRICS_DIM }, () => 9))
  return {
    obs,
    scalars,
    aMove: Array.from({ length: n }, () => 1),
    aFire: Array.from({ length: n }, (_, i) => i % 2),
    lpMove: Array.from({ length: n }, () => -0.5),
    lpFire: Array.from({ length: n }, () => -0.25),
    value: Array.from({ length: n }, () => 0.1),
    metrics,
    done: [...Array.from({ length: n - 1 }, () => 0), 1],
    mask: Array.from({ length: n * MASK_DIM }, () => 1),
    n,
  }
}

const MANIFEST = { schemaMajor: 3, stage: 0, seed: 1, wver: 'f'.repeat(64) }

describe('pack-memory 与写盘路径等价（① 函数级 / ② 组装级）', () => {
  it('① shardNpyEntries 与 writeRlShard 落盘文件逐字节相等，且名字集合/顺序 == RL_SHARD_FILES', () => {
    const dir = mkdtempSync(join(tmpdir(), 'packmem-fn-'))
    const shard = makeShard()
    writeRlShard(dir, shard, MANIFEST)

    const mem = shardNpyEntries(shard)
    expect(mem.map((e) => e.name)).toEqual([...RL_SHARD_FILES])

    for (const e of mem) {
      const onDisk = readFileSync(join(dir, e.name))
      expect(e.data.equals(onDisk)).toBe(true)
      expect(e.data.length).toBe(onDisk.length)
    }
    // 反向：盘上文件集必须**恰好**是 RL_SHARD_FILES + manifest.json（多一个都会进 pack 或说明清单滞后）
    expect(existsSync(join(dir, 'manifest.json'))).toBe(true)
    expect(readdirSync(dir).sort()).toEqual([...RL_SHARD_FILES, 'manifest.json'].sort())
  })

  it('② buildPack(内存 entries) === buildPack(写盘 entries) 逐字节', () => {
    const dir = mkdtempSync(join(tmpdir(), 'packmem-pack-'))
    const shard = makeShard()
    writeRlShard(dir, shard, MANIFEST)

    const memEntries = shardNpyEntries(shard)
    const diskEntries = RL_SHARD_FILES.map((name) => ({
      name,
      data: readFileSync(join(dir, name)),
    }))
    const a = buildPack(MANIFEST as unknown as Record<string, unknown>, memEntries)
    const b = buildPack(MANIFEST as unknown as Record<string, unknown>, diskEntries)
    expect(a.equals(b)).toBe(true)
    expect(a.length).toBeGreaterThan(0)
  })
})

describe('pack-memory 端到端（③ 真跑 exporter 两次）', () => {
  it('同权重/关卡/种子下两条路径的包内容逐字节同一（entries 全量 + manifest 除墙钟 elapsedSec）；内存模式不写 npy 目录', () => {
    // h=16/d=2 瘦身规格（fixtures/student-golden.json）：forward 便宜，且不触发 h64/d8 加速后端，
    // 与本测试关心的 pack 组装无关 —— 只要求「同一局跑出同一个 shard」。
    const g = JSON.parse(
      readFileSync(join(import.meta.dir, 'fixtures', 'student-golden.json'), 'utf8'),
    ) as { h: number; d: number; params: Record<string, unknown> }
    const base = join(process.cwd(), 'tmp')
    mkdirSync(base, { recursive: true })
    const work = mkdtempSync(join(base, 'packmem-e2e-'))
    const weights = join(work, 'weights.json')
    writeFileSync(
      weights,
      JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params }),
    )

    const run = (variant: 'disk' | 'memory'): string => {
      const out = join(work, variant)
      const pack = join(work, `${variant}.pack`)
      const args = [
        'tools/sim/export-rl-rollout.ts',
        '--weights',
        weights,
        '--out',
        out,
        '--stages',
        '0',
        '--seeds',
        '1',
        '--max-ticks',
        '1200',
        '--lives-override',
        '1',
        '--wver',
        MANIFEST.wver,
        '--node-label',
        'packmem-test',
        '--pack',
        pack,
      ]
      if (variant === 'memory') args.push('--pack-memory')
      const r = spawnSync(process.execPath, args, {
        cwd: process.cwd(),
        encoding: 'utf8',
        timeout: 120_000,
        windowsHide: true,
      })
      if (r.status !== 0 || !existsSync(pack)) {
        throw new Error(
          `exporter(${variant}) 失败 rc=${r.status}\n${String(r.stdout ?? '').slice(-800)}\n${String(r.stderr ?? '').slice(-800)}`,
        )
      }
      return pack
    }

    const diskPack = run('disk')
    const memPack = run('memory')

    // 行为差异：内存模式不落 npy 目录，写盘模式落
    expect(existsSync(join(work, 'disk', 'rl_s0_seed1', 'obs.npy'))).toBe(true)
    expect(existsSync(join(work, 'memory', 'rl_s0_seed1', 'obs.npy'))).toBe(false)

    // ⚠ 不能直接比 pack 字节：pack 的 manifest 里带 `elapsedSec`（墙钟，0.1s 粒度）——
    // 那是溯源戳，**本来就不确定**（2026-09-21 实测：并行负载下两条路径 0.3s vs 0.2s ⇒
    // 包字节不等，而 10 个 npy 逐字节相同）。所以可确定的边界是：
    //   entries 全部逐字节 + manifest 除 `elapsedSec` 外逐字段。
    const unDisk = unpackContainer(readFileSync(diskPack))
    const unMem = unpackContainer(readFileSync(memPack))
    expect([...unMem.entries.keys()]).toEqual([...RL_SHARD_FILES])
    for (const name of RL_SHARD_FILES) {
      const onDisk = readFileSync(join(work, 'disk', 'rl_s0_seed1', name))
      const memData = unMem.entries.get(name)!
      expect(memData.length).toBeGreaterThan(0)
      expect(memData.equals(unDisk.entries.get(name)!)).toBe(true)
      expect(memData.equals(onDisk)).toBe(true)
    }
    const mDisk = { ...unDisk.manifest } as Record<string, unknown>
    const mMem = { ...unMem.manifest } as Record<string, unknown>
    expect(typeof mDisk.elapsedSec).toBe('number') // 确证它确实是那条唯一被排除的字段
    delete mDisk.elapsedSec
    delete mMem.elapsedSec
    expect(mMem).toEqual(mDisk)
    expect(mDisk.mode).toBe('rollout')
    expect(mDisk.wver).toBe(MANIFEST.wver)

    // 全绿才清理（失败时留现场供查）
    rmSync(work, { recursive: true, force: true })
  })
})
