/**
 * config-stage.test.ts — 课程自定义关解码（M1d，plan/rl-training-config.md §5）。
 *
 * 覆盖四守卫中的解码契约：13×13 → 26×26、enemyCount 恒显式（守卫③）、
 * 出生点 2×2 冲突拒绝、非法 grid 拒绝。dodge 强制 off 与短路（守卫①/④）由
 * export-rl-rollout 的 smoke 冒烟覆盖（tools/sim 集成路径）。
 */
import { describe, expect, test } from 'bun:test'
import { decodeStageGrid } from '../src/nn/config-stage'
import type { StageJson } from '../src/nn/config-stage'

const GRID_OPEN: number[][] = Array.from({ length: 13 }, () => Array.from({ length: 13 }, () => 0))
// 底行（row 12）：基地 15 居中（col 6），门位砖 1 在 col 4 / col 7
GRID_OPEN[12][6] = 15
GRID_OPEN[12][4] = 1
GRID_OPEN[12][7] = 1

const STAGE: StageJson = {
  name: 't',
  grid: GRID_OPEN,
  forces: 'cccccccccccccccccccc',
  count: 20,
  player_spawn: { col: 10, row: 24 }, // 基地门位（与 2×2 base 不重叠）
  enemy_spawns: [
    { col: 6, row: 1 },
    { col: 12, row: 1 },
    { col: 18, row: 1 },
  ],
}

describe('decodeStageGrid (course custom stage, plan §5)', () => {
  test('13×13 grid decodes to 26×26 tiles + enemyCount explicit', () => {
    const st = decodeStageGrid(STAGE, 2000)
    expect(st.tiles.length).toBe(26)
    expect(st.tiles.every((row) => row.length === 26)).toBe(true)
    // 基地 15 码 → 2×2 'E' 在 (col 12..13, row 24..25)（BASE_POS 对齐）
    expect(st.tiles[24][12]).toBe('E')
    expect(st.tiles[25][13]).toBe('E')
    expect(st.enemies.length).toBe(20)
    expect(st.enemyCount).toBe(20) // 守卫③：恒显式
    expect(st.id).toBe(2000)
    expect(st.playerSpawn).toEqual({ col: 10, row: 24 })
    expect(st.enemySpawns).toEqual([
      { col: 6, row: 1 },
      { col: 12, row: 1 },
      { col: 18, row: 1 },
    ])
  })

  test('default enemyCount derives from forces length', () => {
    // 课程配置省略 count 时 → 敌人总数 = forces 长度（缺省 20 字串 → 20）
    const st = decodeStageGrid({ ...STAGE, count: undefined, forces: 'abcd' }, 2001)
    expect(st.enemyCount).toBe(4)
    expect(decodeStageGrid({ ...STAGE, count: undefined }, 2001).enemyCount).toBe(20)
  })

  test('spawn overlapping brick/base rejected', () => {
    expect(
      () => decodeStageGrid({ ...STAGE, player_spawn: { col: 8, row: 24 } }, 2000), // 压在 door-side brick
    ).toThrow(/solid|基地/)
    expect(
      () => decodeStageGrid({ ...STAGE, player_spawn: { col: 12, row: 24 } }, 2000), // 压基地
    ).toThrow(/基地/)
  })

  test('illegal grid shape/forced-code rejected', () => {
    expect(() => decodeStageGrid({ ...STAGE, grid: GRID_OPEN.slice(0, 12) }, 2000)).toThrow(/13×13/)
    const bad = GRID_OPEN.map((r) => r.slice())
    bad[0][0] = 99
    expect(() => decodeStageGrid({ ...STAGE, grid: bad }, 2000)).toThrow(/瓦码/)
  })

  test('string JSON parses; malformed JSON rejected', () => {
    const st = decodeStageGrid(JSON.stringify(STAGE), 2000)
    expect(st.tiles.length).toBe(26)
    expect(() => decodeStageGrid('{oops', 2000)).toThrow(/不是合法 JSON/)
  })
})
