import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'fs'
import { parseCourseJsonc, stripTrailingCommas } from '../tools/sim/eval-course-ckpt'

describe('eval-course-ckpt JSONC 管线', () => {
  it('干净 JSON 原样通过', () => {
    const src = '{"a": 1, "b": [1, 2], "c": {"d": "x"}}'
    expect(stripTrailingCommas(src)).toBe(src)
    expect(parseCourseJsonc(src)).toEqual({ a: 1, b: [1, 2], c: { d: 'x' } })
  })

  it('尾逗号（同行/跨行/嵌套）全去，中部逗号不动', () => {
    const src = `{
      // 注释含 ,] 括号也不影响
      "grid": [
        [6, 6],
        [6, 0],
      ],
      "spawns": [
        { "col": 2 },
      ],
      "n": 3,
    }`
    const v = parseCourseJsonc(src) as Record<string, unknown>
    expect(v).toEqual({
      grid: [
        [6, 6],
        [6, 0],
      ],
      spawns: [{ col: 2 }],
      n: 3,
    })
  })

  it('字符串内的 ,]/,} 与转义引号原样保留', () => {
    const src = '{"a": "x,]y},", "b": "q\\"],", "c": [1, 2,]}'
    const v = parseCourseJsonc(src) as Record<string, unknown>
    expect(v).toEqual({ a: 'x,]y},', b: 'q"],', c: [1, 2] })
  })

  // 在训课程文件在，锚才有意义；文件若被归档改名则跳过（逻辑覆盖靠上面三例）。
  it.if(existsSync('nn-training/curricula/c6-pickup.jsonc'))(
    '实物锚：训练用 c6-pickup.jsonc（含尾逗号）可解析且与 Python 口径一致',
    () => {
      const text = readFileSync('nn-training/curricula/c6-pickup.jsonc', 'utf8')
      // 文件必须真带尾逗号，否则本用例失去回归意义
      expect(stripTrailingCommas(text) === text).toBe(false)
      const v = parseCourseJsonc(text) as {
        stages: Array<{ forces: string }>
        player: { lives: number }
        reward: { params: { wPickup: number } }
      }
      expect(v.stages[0]?.forces).toBe('abcdabcdabcdabcdabcd')
      expect(v.player.lives).toBe(1)
      expect(v.reward.params.wPickup).toBe(3.0)
    },
  )
})
