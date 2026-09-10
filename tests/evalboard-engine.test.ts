/** evalboard-engine.test.ts ↔ tools/training/evalboard/engine.ts + codehash-files.ts（§2.5）。 */
import { describe, expect, it } from 'bun:test'
import { createHash } from 'node:crypto'
import {
  collectSpecEntries,
  engineEpoch,
  GAMEPLAY_SPECS,
  gameplayFingerprint,
} from '../tools/training/evalboard/engine'
import { REPO_ROOT } from '../tools/agent/codehash-files'

describe('engine_epoch (§2.5)', () => {
  it('gameplay 集含引擎/config/RNG/God + 评估脚本 + golden', () => {
    expect(GAMEPLAY_SPECS).toContain('src/game/')
    expect(GAMEPLAY_SPECS).toContain('src/config/')
    expect(GAMEPLAY_SPECS).toContain('src/utils/')
    expect(GAMEPLAY_SPECS).toContain('src/ai/')
    expect(GAMEPLAY_SPECS).toContain('tools/sim/export-eval-game.ts')
  })
  it('指纹确定性：两次展开逐字节一致', () => {
    const a = gameplayFingerprint(REPO_ROOT)
    const b = gameplayFingerprint(REPO_ROOT)
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    expect(collectSpecEntries(GAMEPLAY_SPECS, REPO_ROOT).length).toBeGreaterThan(100)
  })
  it('engineEpoch = sha256(git + "\\n" + gameplay)[0:16]（与 dist_common 同式）', () => {
    const fp = gameplayFingerprint(REPO_ROOT)
    const want = createHash('sha256').update(`abc123\n${fp}`).digest('hex').slice(0, 16)
    expect(engineEpoch('abc123', fp)).toBe(want)
    expect(engineEpoch('abc123', fp)).toMatch(/^[0-9a-f]{16}$/)
  })
})
