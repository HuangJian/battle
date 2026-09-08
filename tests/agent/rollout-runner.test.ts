import { describe, expect, it } from 'bun:test'
import { mkdtempSync, existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  MIN_NODE_MAJOR,
  NODE_FAIL_LIMIT,
  EngineChoice,
  choiceValid,
  chooseByBench,
  bundleNameFor,
  bundlePathFor,
  createRolloutRunner,
  detectBestNode,
  detectNode,
  ensureNodeBundle,
  parseNodeVersion,
  prepareBundleDir,
} from '../../tools/agent/rollout-runner'

const ENTRY_RL = 'tools/sim/export-rl-rollout.ts'

describe('parseNodeVersion', () => {
  it('解析 vX.Y.Z 与裸 X.Y.Z', () => {
    expect(parseNodeVersion('v26.8.1\n')).toEqual({ major: 26, minor: 8, patch: 1 })
    expect(parseNodeVersion('22.22.2')).toEqual({ major: 22, minor: 22, patch: 2 })
  })
  it('垃圾输入返回 null（不抛）', () => {
    expect(parseNodeVersion('')).toBeNull()
    expect(parseNodeVersion('not a version')).toBeNull()
    expect(parseNodeVersion('vX')).toBeNull()
  })
})

describe('detectNode', () => {
  const run = (status: number, stdout: string) => () => ({ status, stdout })
  it('版本达标才采用', () => {
    expect(detectNode({ bin: 'node', run: run(0, 'v26.8.1') })?.major).toBe(26)
    expect(detectNode({ bin: 'node', run: run(0, 'v22.0.0') })?.major).toBe(22)
  })
  it(`低于 v${MIN_NODE_MAJOR} / 非零退出 → null`, () => {
    expect(detectNode({ bin: 'node', run: run(0, 'v18.20.4') })).toBeNull()
    expect(detectNode({ bin: 'node', run: run(1, 'v26.8.1') })).toBeNull()
    expect(detectNode({ bin: 'node', run: run(0, 'command not found') })).toBeNull()
  })
  it('执行器抛异常 → null（node 不存在时不炸启动）', () => {
    expect(
      detectNode({
        bin: 'node',
        run: () => {
          throw new Error('ENOENT')
        },
      }),
    ).toBeNull()
  })
})

describe('detectBestNode', () => {
  const versions: Record<string, string> = {
    '/old/node': 'v22.22.2',
    '/new/node': 'v26.8.1',
    '/ancient/node': 'v18.20.4',
    '/broken/node': 'nope',
  }
  const run = (bin: string) => ({
    status: versions[bin] && versions[bin] !== 'nope' ? 0 : 1,
    stdout: versions[bin] ?? '',
  })
  it('挑版本最高的可用 node', () => {
    const best = detectBestNode({
      list: () => ['/ancient/node', '/old/node', '/new/node', '/broken/node'],
      run,
    })
    expect(best?.bin).toBe('/new/node')
    expect(best?.version).toBe('v26.8.1')
  })
  it('全部不达标 → null', () => {
    expect(detectBestNode({ list: () => ['/ancient/node'], run })).toBeNull()
  })
  it('显式 bin 不扫描（用于强制指定运行时）', () => {
    expect(detectBestNode({ bin: '/old/node', list: () => ['/new/node'], run })?.bin).toBe(
      '/old/node',
    )
  })
})

describe('bundle 映射', () => {
  it('白名单内映射，白名单外 null', () => {
    expect(bundleNameFor(ENTRY_RL)).toBe('export-rl-rollout')
    expect(bundleNameFor('tools/sim/export-goal-rollout.ts')).toBe('export-goal-rollout')
    expect(bundleNameFor('tools/sim/whatever.ts')).toBeNull()
    expect(bundlePathFor('tools/sim/whatever.ts', '/x')).toBeNull()
    expect(bundlePathFor(ENTRY_RL, '/x')).toBe(path.join('/x', 'export-rl-rollout.mjs'))
  })
})

describe('ensureNodeBundle', () => {
  const setup = () => {
    const repoRoot = mkdtempSync(path.join(tmpdir(), 'rr-repo-'))
    const bundleDir = mkdtempSync(path.join(tmpdir(), 'rr-bundle-'))
    mkdirSync(path.dirname(path.join(repoRoot, ENTRY_RL)), { recursive: true })
    writeFileSync(path.join(repoRoot, ENTRY_RL), '// stub\n')
    mkdirSync(path.join(repoRoot, 'src', 'nn', 'wasm'), { recursive: true })
    writeFileSync(path.join(repoRoot, 'src', 'nn', 'wasm', 'conv_feats.wasm'), 'WASM')
    return { repoRoot, bundleDir }
  }
  const okBuild = (out: string) => {
    writeFileSync(out, '// bundled\n')
    return { status: 0 }
  }

  it('产物 + wasm 资产同时就位（wasm 缺失 = 静默回退 TS 路径的坑）', () => {
    const { repoRoot, bundleDir } = setup()
    const out = ensureNodeBundle(ENTRY_RL, {
      repoRoot,
      bundleDir,
      build: (_entry, out) => okBuild(out),
    })
    expect(out).toBe(path.join(bundleDir, 'export-rl-rollout.mjs'))
    expect(existsSync(out!)).toBe(true)
    // 关键断言：wasm 必须与产物同级 wasm/ 下
    expect(existsSync(path.join(bundleDir, 'wasm', 'conv_feats.wasm'))).toBe(true)
  })

  it('打包失败 → null（调用方回退 bun）', () => {
    const { repoRoot, bundleDir } = setup()
    expect(
      ensureNodeBundle(ENTRY_RL, {
        repoRoot,
        bundleDir,
        build: () => ({ status: 1, stderr: 'boom' }),
      }),
    ).toBeNull()
  })

  it('非白名单入口 → null', () => {
    const { repoRoot, bundleDir } = setup()
    expect(
      ensureNodeBundle('tools/sim/nope.ts', {
        repoRoot,
        bundleDir,
        build: (_e, o) => okBuild(o),
      }),
    ).toBeNull()
  })
})

describe('prepareBundleDir', () => {
  it('codeHash 变化 → 清掉旧产物', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'rr-stale-'))
    writeFileSync(path.join(dir, 'export-rl-rollout.mjs'), '// old\n')
    prepareBundleDir(dir, 'hash-A')
    writeFileSync(path.join(dir, 'export-rl-rollout.mjs'), '// old\n')
    prepareBundleDir(dir, 'hash-B')
    expect(existsSync(path.join(dir, 'export-rl-rollout.mjs'))).toBe(false)
    expect(readFileSync(path.join(dir, '.codehash'), 'utf8')).toBe('hash-B')
  })
})

describe('createRolloutRunner', () => {
  const setup = () => {
    const repoRoot = mkdtempSync(path.join(tmpdir(), 'rr-repo2-'))
    const bundleDir = mkdtempSync(path.join(tmpdir(), 'rr-bundle2-'))
    mkdirSync(path.dirname(path.join(repoRoot, ENTRY_RL)), { recursive: true })
    writeFileSync(path.join(repoRoot, ENTRY_RL), '// stub\n')
    mkdirSync(path.join(repoRoot, 'src', 'nn', 'wasm'), { recursive: true })
    writeFileSync(path.join(repoRoot, 'src', 'nn', 'wasm', 'conv_feats.wasm'), 'WASM')
    return { repoRoot, bundleDir }
  }
  const opts = (repoRoot: string, bundleDir: string, extra: Record<string, unknown> = {}) => ({
    repoRoot,
    bundleDir,
    codeHash: 'h1',
    detect: () => ({
      bin: 'node',
      version: 'v26.8.1',
      major: 26,
      minor: 8,
      patch: 1,
      rank: 26_008_001,
    }),
    build: (_entry: string, out: string) => {
      writeFileSync(out, '// bundled\n')
      return { status: 0 }
    },
    bench: () => ({ bunMs: 8.0, nodeMs: 5.0 }),
    ...extra,
  })

  it('有可用 node → 子进程用 node 跑打包产物，argv 首参换成产物路径', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir))
    expect(r.engine).toBe('node')
    const plan = r.launch(ENTRY_RL, [ENTRY_RL, '--weights', 'w.json'])
    expect(plan.engine).toBe('node')
    expect(plan.cmd).toBe('node')
    expect(plan.argv[0]).toBe(path.join(bundleDir, 'export-rl-rollout.mjs'))
    // 除首参（源码 → 产物）外其余 argv 原样透传
    expect(plan.argv.slice(1)).toEqual(['--weights', 'w.json'])
  })

  it('无 node → 原 bun 路径（argv 完全不变）', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir, { detect: () => null }))
    expect(r.engine).toBe('bun')
    const args = [ENTRY_RL, '--weights', 'w.json']
    const plan = r.launch(ENTRY_RL, args)
    expect(plan.cmd).toBe(process.execPath)
    expect(plan.argv).toEqual(args)
  })

  it('forceBun 优先于 node 可用（--no-node 回滚开关）', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir, { forceBun: true }))
    expect(r.engine).toBe('bun')
    expect(r.launch(ENTRY_RL, [ENTRY_RL]).cmd).toBe(process.execPath)
  })

  it('打包全失败 → 自动回落 bun', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir, { build: () => ({ status: 1 }) }))
    expect(r.engine).toBe('bun')
  })

  it(`node 连续失败 ${NODE_FAIL_LIMIT} 次 → 永久回退 bun（失败不误伤单次任务失败）`, () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir))
    r.noteFailure('node', 'rc=1')
    expect(r.engine).toBe('node') // 1 次不算
    r.noteSuccess('node')
    r.noteFailure('node', 'rc=1')
    expect(r.engine).toBe('node') // 成功重置计数
    r.noteFailure('node', 'rc=1')
    r.noteFailure('node', 'rc=1')
    expect(r.engine).toBe('bun')
    expect(r.launch(ENTRY_RL, [ENTRY_RL]).engine).toBe('bun')
  })

  it('非白名单入口即使 engine=node 也走 bun', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir))
    expect(r.launch('tools/sim/other.ts', ['tools/sim/other.ts']).engine).toBe('bun')
  })
})

describe('引擎微基准自动选择（§374）', () => {
  const setup = () => {
    const repoRoot = mkdtempSync(path.join(tmpdir(), 'rr-bench-'))
    const bundleDir = mkdtempSync(path.join(tmpdir(), 'rr-benchb-'))
    mkdirSync(path.dirname(path.join(repoRoot, ENTRY_RL)), { recursive: true })
    writeFileSync(path.join(repoRoot, ENTRY_RL), '// stub\n')
    mkdirSync(path.join(repoRoot, 'src', 'nn', 'wasm'), { recursive: true })
    writeFileSync(path.join(repoRoot, 'src', 'nn', 'wasm', 'conv_feats.wasm'), 'WASM')
    return { repoRoot, bundleDir }
  }
  const node = (v = 'v26.8.1') => ({
    bin: 'node',
    version: v,
    major: 26,
    minor: 8,
    patch: 1,
    rank: 26_008_001,
  })
  const opts = (repoRoot: string, bundleDir: string, extra: Record<string, unknown> = {}) => ({
    repoRoot,
    bundleDir,
    codeHash: 'h1',
    detect: () => node(),
    build: (_e: string, o: string) => {
      writeFileSync(o, '// bundle\n')
      return { status: 0 }
    },
    ...extra,
  })

  it('chooseByBench：node 快 ≥3% 才选 node（迟滞）', () => {
    expect(chooseByBench(8.0, 5.0)).toBe('node') // 快 37%
    expect(chooseByBench(8.0, 7.9)).toBe('bun') // 快 1.3% < 3% → bun
    expect(chooseByBench(8.0, 8.0)).toBe('bun')
    expect(chooseByBench(8.0, null)).toBe('bun')
    expect(chooseByBench(8.0, 0)).toBe('bun')
  })

  it('choiceValid：bun/node 版本或 wasm 变了即失效', () => {
    const c: EngineChoice = {
      winner: 'node',
      bunMs: 8,
      nodeMs: 5,
      bunVer: '1.4.2',
      nodeVer: 'v26.8.1',
      wasmSha: 'abc',
      ts: 1,
    }
    expect(choiceValid(c, '1.4.2', 'v26.8.1', 'abc')).toBe(true)
    expect(choiceValid(c, '1.4.3', 'v26.8.1', 'abc')).toBe(false)
    expect(choiceValid(c, '1.4.2', 'v26.8.2', 'abc')).toBe(false)
    expect(choiceValid(c, '1.4.2', 'v26.8.1', 'abd')).toBe(false)
    expect(choiceValid(null, '1.4.2', 'v26.8.1', 'abc')).toBe(false)
  })

  it('node 明显更快 → 选 node 且落缓存；二次创建读缓存不再跑基准', () => {
    const { repoRoot, bundleDir } = setup()
    let benchCalls = 0
    const mk = (bench: () => { bunMs: number; nodeMs: number } | null) =>
      createRolloutRunner(opts(repoRoot, bundleDir, { bench }))
    const r1 = mk(() => {
      benchCalls++
      return { bunMs: 8.0, nodeMs: 5.0 }
    })
    expect(r1.engine).toBe('node')
    expect(benchCalls).toBe(1)
    const r2 = mk(() => {
      benchCalls++
      return { bunMs: 8.0, nodeMs: 999 }
    }) // 缓存命中→不应再测
    expect(r2.engine).toBe('node') // 沿用缓存
    expect(benchCalls).toBe(1)
  })

  it('node 不达标(迟滞内) → 选 bun', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(
      opts(repoRoot, bundleDir, { bench: () => ({ bunMs: 8.0, nodeMs: 8.1 }) }),
    )
    expect(r.engine).toBe('bun')
  })

  it('微基准失败 → 回退 bun', () => {
    const { repoRoot, bundleDir } = setup()
    const r = createRolloutRunner(opts(repoRoot, bundleDir, { bench: () => null }))
    expect(r.engine).toBe('bun')
  })

  it('forceNode 直接选 node（跳过基准）', () => {
    const { repoRoot, bundleDir } = setup()
    let called = false
    const r = createRolloutRunner(
      opts(repoRoot, bundleDir, {
        forceNode: true,
        bench: () => {
          called = true
          return { bunMs: 1, nodeMs: 1 }
        },
      }),
    )
    expect(r.engine).toBe('node')
    expect(called).toBe(false)
  })
})
