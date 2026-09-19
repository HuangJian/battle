import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync } from 'fs'
import {
  buildCourseJobs,
  buildRemoteTaskUrl,
  bunMajorMinor,
  manifestToCourseRow,
  nodeGateReason,
  parseCourseJsonc,
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

describe('eval-course-ckpt dist 映射', () => {
  const weights = [
    { path: 'tmp/a.json', label: 'bc' },
    { path: 'tmp/b.json', label: 'it30' },
  ]

  it('buildCourseJobs：stageLocal/seed/stageId 与本地口径一致', () => {
    // 3 关 × games=7 seed0=100：g=0..6 → stage 0,1,2,0,1,2,0；seed = 100 + floor(g/3)
    const jobs = buildCourseJobs(weights, 7, 100, 3)
    expect(jobs.length).toBe(14)
    expect(jobs[0]).toMatchObject({
      id: 0,
      weightIdx: 0,
      label: 'bc',
      stageLocal: 0,
      seed: 100,
      stageId: 2000,
    })
    expect(jobs[2]).toMatchObject({ stageLocal: 2, seed: 100, stageId: 2002 })
    expect(jobs[3]).toMatchObject({ stageLocal: 0, seed: 101, stageId: 2000 })
    expect(jobs[6]).toMatchObject({ stageLocal: 0, seed: 102, stageId: 2000 })
    // 第二份权重 id 连续
    expect(jobs[7]).toMatchObject({ id: 7, weightIdx: 1, label: 'it30', stageLocal: 0, seed: 100 })
  })

  it('buildRemoteTaskUrl：mode=eval + stageJson + lives/level', () => {
    const url = buildRemoteTaskUrl({
      baseUrl: 'http://127.0.0.1:8443/',
      iterId: 'evalcourse-1',
      wver: 'abc',
      kind: 'rollout',
      stageId: 2001,
      seed: 42,
      maxTicks: 12000,
      difficulty: 'hard',
      policy: 'nn',
      stageJson: '{"name":"s1","tiles26":[]}',
      livesOverride: 1,
      playerLevel: 0,
    })
    expect(url.startsWith('http://127.0.0.1:8443/v1/task?')).toBe(true)
    const q = new URL(url).searchParams
    expect(q.get('mode')).toBe('eval')
    expect(q.get('kind')).toBe('rollout')
    expect(q.get('stage')).toBe('2001')
    expect(q.get('seed')).toBe('42')
    expect(q.get('stageJson')).toBe('{"name":"s1","tiles26":[]}')
    expect(q.get('livesOverride')).toBe('1')
    expect(q.get('playerLevel')).toBe('0')
    expect(q.get('policy')).toBeNull() // nn 不下发 policy
  })

  it('buildRemoteTaskUrl：god 策略带 policy + kind=none', () => {
    const url = buildRemoteTaskUrl({
      baseUrl: 'http://n',
      iterId: 'x',
      wver: 'deadbeef',
      kind: 'none',
      stageId: 2000,
      seed: 0,
      maxTicks: 36000,
      difficulty: 'hard',
      policy: 'god',
      stageJson: '{}',
      livesOverride: 3,
      playerLevel: 0,
    })
    const q = new URL(url).searchParams
    expect(q.get('kind')).toBe('none')
    expect(q.get('policy')).toBe('god')
  })

  it('manifestToCourseRow：Phase0 与击杀字段透传', () => {
    const task = buildCourseJobs([{ path: '', label: 'god' }], 1, 0, 1)[0]!
    const row = manifestToCourseRow(
      {
        outcome: 'stage_clear',
        win: true,
        cleared: true,
        ticks: 800,
        kills: 20,
        enemyHits: 30,
        playerHits: 2,
        playerDamageTaken: 4,
        playerShots: 40,
        powerUpsCollected: 1,
        score: 12.5,
        hitsByKind: [1, 2, 3, 4],
        killsByKind: [10, 5, 3, 2],
        exposureByKind: [100, 50, 20, 10],
        firstHitKind: 'basic',
        firstKillKind: 'fast',
        killOrder: ['basic', 'fast'],
        killerKinds: ['armor', null],
      },
      task,
      'arena-a',
    )
    expect(row).toMatchObject({
      label: 'god',
      stageId: 2000,
      stageName: 'arena-a',
      outcome: 'stage_clear',
      win: true,
      cleared: true,
      kills: 20,
      hitsByKind: [1, 2, 3, 4],
      killsByKind: [10, 5, 3, 2],
      firstHitKind: 'basic',
      firstKillKind: 'fast',
      killOrder: ['basic', 'fast'],
      killerKinds: ['armor', null],
    })
  })

  it('manifestToCourseRow：缺 Phase0 键时填零，不抛错', () => {
    const task = buildCourseJobs([{ path: '', label: 'nn' }], 1, 5, 2)[0]!
    const row = manifestToCourseRow({ outcome: 'gameover', win: false }, task, 's0')
    expect(row.seed).toBe(5)
    expect(row.stageId).toBe(2000)
    expect(row.hitsByKind).toEqual([0, 0, 0, 0])
    expect(row.killOrder).toEqual([])
    expect(row.killerKinds).toEqual([])
  })

  it('nodeGateReason：能力位 / bun / codeHash', () => {
    expect(nodeGateReason(null, '1.2', 'aa')).toBe('ping failed')
    expect(
      nodeGateReason(
        { evalSupport: false, stageJsonSupport: true, bunVersion: '1.2' },
        '1.2',
        'aa',
      ),
    ).toBe('lacks evalSupport')
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: false, bunVersion: '1.2' },
        '1.2',
        'aa',
      ),
    ).toBe('lacks stageJsonSupport')
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: true, bunVersion: '1.3.9' },
        '1.2',
        'aa',
      ),
    ).toBe('bun version mismatch')
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: true, bunVersion: '1.2.1', codeHash: 'bb' },
        '1.2',
        'aa',
      ),
    ).toMatch(/codeHash mismatch/)
    expect(
      nodeGateReason(
        { evalSupport: true, stageJsonSupport: true, bunVersion: '1.2.1', codeHash: 'aa' },
        '1.2',
        'aa',
      ),
    ).toBeNull()
  })

  it('bunMajorMinor', () => {
    expect(bunMajorMinor('1.2.3')).toBe('1.2')
    expect(bunMajorMinor('1.1.38')).toBe('1.1')
  })
})
