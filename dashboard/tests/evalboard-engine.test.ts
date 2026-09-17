/** evalboard-engine.test.ts ↔ dashboard/src/evalboard/engine.ts + codehash-files.ts（§2.5）。
 *
 * 2026-09-17 统一：eval 节点门与 rollout 门同源 = tools/agent/codehash-files.txt
 * （engine_epoch = sha256(codeHash)[0:16]，不再掺 git commit，也不再另立 GAMEPLAY_SPECS）。
 */
import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import {
  collectCodeHashEntries,
  computeCodeHash,
  computeEngineEpoch,
  engineEpoch,
  gitCommit,
  REPO_ROOT,
} from '../src/evalboard/engine'

describe('engine_epoch (§2.5；2026-09-17 统一到 SSOT 清单)', () => {
  it('epoch = sha256(codeHash)[0:16]（与 dist_common.compute_engine_epoch 同式）', () => {
    const ch = computeCodeHash()
    const want = createHash('sha256').update(ch).digest('hex').slice(0, 16)
    expect(engineEpoch(ch)).toBe(want)
    expect(engineEpoch(ch)).toMatch(/^[0-9a-f]{16}$/)
    // 一次展开同时给出 epoch 与 codeHash（诊断时可比）
    expect(computeEngineEpoch()).toEqual({ engine_epoch: want, codeHash: ch })
  })

  it('codeHash 覆盖 eval 引擎面（game/config/RNG/God + golden），且排除无关树', () => {
    const rels = collectCodeHashEntries().map((e) => e.relPath)
    for (const spec of ['src/game/', 'src/config/', 'src/utils/', 'src/ai/']) {
      expect(rels.some((r) => r.startsWith(spec))).toBe(true)
    }
    expect(rels).toContain('tools/det-golden.v1.sha256')
    expect(rels).toContain('tools/sim/export-eval-game.ts')
    // 与 rollout/eval 无关的树入集 = 无关提交即触发全节点重启波（用户指令禁区）
    expect(rels.some((r) => r.startsWith('dashboard/') || r.startsWith('nn-training/'))).toBe(false)
  })

  it('git_commit 仅作观测字段，不参与 epoch（无关提交不再让节点 stale）', () => {
    expect(gitCommit(REPO_ROOT)).toMatch(/^[0-9a-f]{7,40}$|^nogit$/)
    expect(computeEngineEpoch().engine_epoch).toBe(engineEpoch(computeCodeHash()))
  })
})
