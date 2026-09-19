/**
 * eval-course-ckpt.test.ts ↔ tools/sim/eval-course-ckpt.ts + tools/lib/hybrid-batch.ts
 *
 * 2026-09-19 重构后：节点通信/重试/探测全部搬去 Python（`nn-training/eval_course_once.py`
 * → `rl.batch_eval.BatchEvalRunner`），本文件只覆盖 TS 侧仍然拥有的东西：
 *   * 课程 JSONC 解析（与 Python `rl/jsonc.py` 同口径，两端都读同一批关卡文件）
 *   * `--weights label=path` 解析、spec 构造（本机份额/noNodes/dist 配置如何透传）
 *   * `TailRaceBatch` 的纯逻辑（Python 队列的 TS 镜像）
 */
import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'fs'
import { TailRaceBatch } from '../tools/lib/hybrid-batch'
import {
  buildSpec,
  parseCourseJsonc,
  parseWeightSpec,
  stripTrailingCommas,
} from '../tools/sim/eval-course-ckpt'

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

describe('spec 构造（透传给 Python 引擎的参数）', () => {
  it('--weights 解析：label=path 与裸路径', () => {
    expect(parseWeightSpec('it30=tmp/w.json')).toEqual({ path: 'tmp/w.json', label: 'it30' })
    expect(parseWeightSpec('tmp/w.json')).toEqual({ path: 'tmp/w.json', label: 'w.json' })
  })

  it('本机份额 / 无节点 / dist 配置都进 spec；未指定则**不写**该键（让 Python 读配置）', () => {
    const base = {
      course: 'nn-training/levels/ladder-c06.jsonc',
      weights: [{ label: 'it30', path: 'tmp/w.json' }],
      games: 8,
      seed0: 405000,
      policy: 'nn' as const,
      iterId: 't1',
      runDir: 'tmp/x.run',
      out: 'tmp/x.jsonl',
      noNodes: false,
    }
    const bare = buildSpec(base)
    expect('localSlots' in bare).toBe(false) // 缺省 = 配置（policy.evalLocalSlots → rl.local_slots）
    expect('distCfgPath' in bare).toBe(false)

    const explicit = buildSpec({
      ...base,
      localSlots: 0,
      distCfgPath: 'nn-training/rl-config.json',
      noNodes: true,
    })
    expect(explicit.localSlots).toBe(0)
    expect(explicit.distCfgPath).toBe('nn-training/rl-config.json')
    expect(explicit.noNodes).toBe(true)
    expect(explicit.policy).toBe('nn')
  })
})

describe('TailRaceBatch.cursorDone（Python 侧 rescan 停止条件的 TS 镜像）', () => {
  it('游标发完前 false，发完后 true（含尾竞速阶段）', () => {
    const b = new TailRaceBatch(3)
    expect(b.cursorDone).toBe(false)
    b.consumer(1)
    expect(b.claim(false)).toBe(0)
    expect(b.cursorDone).toBe(false)
    expect(b.claim(false)).toBe(1)
    expect(b.claim(false)).toBe(2)
    expect(b.cursorDone).toBe(true)
  })
})
