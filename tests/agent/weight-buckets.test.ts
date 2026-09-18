import { describe, expect, it } from 'bun:test'
import {
  bucketKey,
  findSha,
  kindOfBucketKey,
  latestOfKind,
  splitBucketKey,
} from '../../tools/agent/weight-buckets'

interface W {
  sha: string
  file: string
}

/** 造一个「已发布过」的桶组：courses → 该课程的 sha 列表（按发布序）。 */
function publish(spec: Record<string, string[]>, kind = 'rollout'): Map<string, Map<string, W>> {
  const buckets = new Map<string, Map<string, W>>()
  for (const [course, shas] of Object.entries(spec)) {
    const b = new Map<string, W>()
    for (const sha of shas) b.set(sha, { sha, file: `weights-${kind}-${sha.slice(0, 16)}.json` })
    buckets.set(bucketKey(course, kind), b)
  }
  return buckets
}

describe('bucketKey', () => {
  it('课程与 kind 都在键里，且可无损还原', () => {
    const k = bucketKey('x1-rebirth-a2', 'rollout')
    expect(k).toBe('x1-rebirth-a2\u0000rollout')
    expect(splitBucketKey(k)).toEqual({ course: 'x1-rebirth-a2', kind: 'rollout' })
    expect(kindOfBucketKey(k)).toBe('rollout')
  })

  it('空课程名 = 旧单课程桶（升级期的兼容锚点）', () => {
    expect(splitBucketKey(bucketKey('', 'rollout')).course).toBe('')
    expect(kindOfBucketKey(bucketKey('', 'goal'))).toBe('goal')
  })

  it('课程名里不可能含分隔符（parse_course_arg 拒空白），键不会歧义', () => {
    // 若课程名真被塞进分隔符，split 仍应把第一个分隔符当分界（而不是崩）
    expect(splitBucketKey('a\u0000b\u0000rollout')).toEqual({ course: 'a', kind: 'b\u0000rollout' })
  })

  it('无分隔符的键（旧格式/脏数据）不抛，尽力而为', () => {
    expect(splitBucketKey('rollout')).toEqual({ course: '', kind: 'rollout' })
  })
})

describe('findSha —— 按 sha 取值（跨桶退让）', () => {
  const shaA = 'a'.repeat(64)
  const shaB = 'b'.repeat(64)

  it('本课桶优先命中', () => {
    const b = publish({ 'course-x': [shaA] })
    expect(findSha(b, 'course-x', 'rollout', shaA)?.file).toBe(
      `weights-rollout-${'a'.repeat(16)}.json`,
    )
  })

  it('本课桶没有 → 退让到旧单课程桶（训练侧先带 X-Course 而后端还在旧桶）', () => {
    const b = publish({ '': [shaA] })
    expect(findSha(b, 'course-x', 'rollout', shaA)?.sha).toBe(shaA)
  })

  it('本课桶与旧桶都没有 → 扫同 kind 的其它课程桶（sha 内容寻址，同一份字节）', () => {
    const b = publish({ 'course-y': [shaA] })
    expect(findSha(b, 'course-x', 'rollout', shaA)?.sha).toBe(shaA)
  })

  it('不同 kind 的桶不互相顶（rollout 的 sha 不会从 intent 桶里取出来）', () => {
    const buckets = new Map<string, Map<string, W>>()
    buckets.set(bucketKey('', 'intent'), new Map([[shaA, { sha: shaA, file: 'intent' }]]))
    expect(findSha(buckets, '', 'rollout', shaA)).toBeNull()
    expect(findSha(buckets, '', 'intent', shaA)?.file).toBe('intent')
  })

  it('未知 sha / 空 sha 一律 null（不静默给一份别的权重）', () => {
    const b = publish({ 'course-x': [shaA] })
    expect(findSha(b, 'course-x', 'rollout', shaB)).toBeNull()
    expect(findSha(b, 'course-x', 'rollout', '')).toBeNull()
  })
})

describe('多课程隔离：一门课的新 sha 不再驱逐另一门课的', () => {
  /** 驱逐规则来自 sampler-agent：超上限时删**最旧**（Map 首键），最新键永不当受害者。 */
  const CAP = 4
  function put(buckets: Map<string, Map<string, W>>, course: string, sha: string): void {
    const k = bucketKey(course, 'rollout')
    let b = buckets.get(k)
    if (!b) {
      b = new Map()
      buckets.set(k, b)
    }
    if (b.has(sha)) return
    b.set(sha, { sha, file: `weights-rollout-${sha.slice(0, 16)}.json` })
    while (b.size > CAP) {
      const victim = b.keys().next().value as string | undefined
      if (victim === undefined) break
      // 最新键（最后一个）永不被驱逐：它就是当前活跃 sha
      const keys = [...b.keys()]
      if (victim === keys[keys.length - 1]) break
      b.delete(victim)
    }
  }

  it('改造前式共享桶会把别的课挤掉；按课程分桶后各自保留完整历史', () => {
    // 共享桶（课程名空串模拟改造前的单 kind 行为）
    const shared = new Map<string, Map<string, W>>()
    const perCourse = new Map<string, Map<string, W>>()
    const old: Record<string, string[]> = { c1: [], c2: [] }
    for (let i = 0; i < 3; i++) {
      for (const c of ['c1', 'c2']) {
        const sha = `${c}-${i}`.padEnd(64, 'x')
        put(shared, '', sha) // 都进同一个桶
        put(perCourse, c, sha)
        old[c]!.push(sha)
      }
    }
    // 共享桶装了 6 个 > CAP=4 ⇒ 最早两个被驱逐（c1-0/c2-0）
    expect(shared.get(bucketKey('', 'rollout'))!.size).toBe(CAP)
    expect(findSha(shared, '', 'rollout', old.c1![0]!)).toBeNull()
    // 分桶后每门课 3 个 ≤ CAP，一个都没丢
    expect(findSha(perCourse, 'c1', 'rollout', old.c1![0]!)?.sha).toBe(old.c1![0]!)
    expect(findSha(perCourse, 'c2', 'rollout', old.c2![0]!)?.sha).toBe(old.c2![0]!)
  })

  it('最新 sha 永不被驱逐（它就是正在派发的那一份）', () => {
    const b = new Map<string, Map<string, W>>()
    for (let i = 0; i < CAP + 3; i++) put(b, 'c1', `s${i}`.padEnd(64, 'y'))
    const bucket = b.get(bucketKey('c1', 'rollout'))!
    expect(bucket.size).toBe(CAP)
    const keys = [...bucket.keys()]
    expect(keys[keys.length - 1]).toBe(`s${CAP + 2}`.padEnd(64, 'y'))
  })
})

describe('latestOfKind', () => {
  const b = publish({ c1: ['a'.repeat(64)], c2: ['b'.repeat(64)] })
  b.set(
    bucketKey('', 'intent'),
    new Map([['i'.repeat(64), { sha: 'i'.repeat(64), file: 'intent' }]]),
  )

  it('只看同 kind 的桶，取最后插入的那个', () => {
    expect(latestOfKind(b, 'rollout')?.sha).toBe('b'.repeat(64))
    expect(latestOfKind(b, 'intent')?.file).toBe('intent')
  })

  it('该 kind 没有桶 → null（不跨越 kind 给一份别的权重）', () => {
    expect(latestOfKind(b, 'goal')).toBeNull()
  })
})
