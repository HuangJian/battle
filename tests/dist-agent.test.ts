import { describe, expect, it } from 'bun:test'
import {
  computeCodeHashFromFiles,
  packContainer,
  unpackContainer,
  SHARD_FILES,
} from '../tools/agent/sampler-agent'

describe('dist sampler-agent helpers (plan/distributed-rollout.md v3.3)', () => {
  it('container roundtrip preserves manifest + files', () => {
    const report = { stage: 3, seed: 42, wver: 'ab'.repeat(32), games: 1, scoreList: [0.5] }
    const files = Object.fromEntries(
      SHARD_FILES.map((n: string, i: number) => [n, Buffer.from([i, 1, 2, 3]).toString('base64')]),
    )
    const buf = packContainer(report, files)
    const back = unpackContainer(buf)
    expect(back.manifest).toEqual(report)
    expect(back.files).toEqual(files)
  })

  it('codeHash is order-independent and content-sensitive', () => {
    const mk = (relPath: string, content: string) => ({ relPath, content: Buffer.from(content) })
    const a = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('a.ts', 'y')])
    const b = computeCodeHashFromFiles([mk('a.ts', 'y'), mk('b.ts', 'x')])
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    const c = computeCodeHashFromFiles([mk('b.ts', 'x!'), mk('a.ts', 'y')])
    expect(c).not.toBe(a)
    const d = computeCodeHashFromFiles([mk('b.ts', 'x'), mk('aa.ts', 'y')])
    expect(d).not.toBe(a) // 路径变化也要改变 hash
  })

  it('shard file set matches ppo.py load_shard expectation (12 npy)', () => {
    const names: string[] = [...SHARD_FILES]
    expect(names.length).toBe(12)
    for (const must of ['obs.npy', 'reward.npy', 'scalars.npy', 'done.npy', 'mask.npy'])
      expect(names).toContain(must)
  })
})
