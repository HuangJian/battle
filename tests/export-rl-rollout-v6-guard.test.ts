import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { assertRolloutWeightsSchema, MASK_DIM, MASK_V6_DIM } from '../tools/sim/export-rl-rollout'

/**
 * ★ B1 v6 导出端的契约自检（2026-10-20）。
 *
 * 背景：v6 shard 与 legacy **不是同一个 schema**（掩码 9 vs 7、多 `a_hold`/`lp_hold` 两个 npy），
 * 而 `np_core._RL_SHARD_SPEC` 的 mask 原先**没有形状校验**（`_RL_SHARD_SHAPES` 只盯
 * obs/scalars/extra）⇒ 7 宽 mask 配 v6 模型会一路静默读到训练完才表现异常。
 * 现在的分工：**导出端声明（本函数）+ 加载端校验（mask 宽度按 a_hold 存在性）**。
 *
 * 本文件锁三件事：
 *   ① 宽度常量本身（7 / 9，且 v6 = move4+fire3+hold2）；
 *   ② 「声明 v6 但推理面没有 holdLogits」必须响亮拒绝（模型与权重不同源）；
 *   ③ legacy 路径零影响（v5 权重照旧）。
 */
const SLIM = join(import.meta.dir, 'fixtures', 'student-golden.json')
const V6 = join(import.meta.dir, 'fixtures', 'student-golden-v6.json')

const fakeHoldLogits = new Float32Array(2)

describe('export-rl-rollout：B1 v6 契约自检', () => {
  it('掩码宽度：legacy 7（move5+fire2）/ v6 9（move4+fire3+hold2）', () => {
    expect(MASK_DIM).toBe(7)
    expect(MASK_V6_DIM).toBe(9)
    // v6 不是"7 + 2"这么随意：它是 4+3+2 —— 与 computeMasks 的字段顺序一致
    expect(MASK_V6_DIM).toBe(4 + 3 + 2)
  })

  it('fixture 自证：v6 权重确实带 hold_head，legacy 确实不带', () => {
    // 防止 fixture 被换成 legacy 后本文件整体变成空转
    expect(readFileSync(V6, 'utf8')).toContain('hold_head')
    expect(readFileSync(SLIM, 'utf8')).not.toContain('hold_head')
  })

  it('声明 v6 但没有 holdLogits ⇒ 响亮拒绝（模型/权重不同源）', () => {
    expect(() => assertRolloutWeightsSchema({ usesHoldFire3: true })).toThrow(/holdLogits/)
    expect(() => assertRolloutWeightsSchema({ usesHoldFire3: true, holdLogits: null })).toThrow(
      /holdLogits/,
    )
  })

  it('声明 v6 且 holdLogits 在场 ⇒ 放行', () => {
    expect(() =>
      assertRolloutWeightsSchema({ usesHoldFire3: true, holdLogits: fakeHoldLogits }),
    ).not.toThrow()
  })

  it('legacy 权重 ⇒ 放行（v5 路径零影响）', () => {
    expect(() => assertRolloutWeightsSchema({ usesHoldFire3: false })).not.toThrow()
    expect(() => assertRolloutWeightsSchema({})).not.toThrow()
    expect(() => assertRolloutWeightsSchema(undefined)).not.toThrow()
  })

  it('只有 === true 才进 v6 分支（不按 truthy 判）', () => {
    expect(() => assertRolloutWeightsSchema({ usesHoldFire3: 1 })).not.toThrow()
    expect(() => assertRolloutWeightsSchema({ usesHoldFire3: 'true' })).not.toThrow()
  })
})
