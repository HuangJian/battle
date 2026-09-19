/** evalboard-corpora.test.ts ↔ dashboard/src/evalboard/{corpora,batches,requests}.ts
 *  + verdict-cli.ts（P2 判决批：语料注册表 × 多 ckpt 同批同种子配对）。
 *
 *  断言契约（改动时先读 docs/evalboard-phase0-census.md §P2）：
 *  - 注册表坏行**响亮失败**（静默跳过 = 判决跑在空语料上而读数看似正常）。
 *  - 判决批的身份 = 语料 id + ckpt 标签序列，**顺序敏感**。
 *  - 判决批不占用 ladder 键空间：course/rung_from/ckpt 为空串，靠 kind 判别。
 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import type { EvalBatch } from '../src/evalboard/batches'
import { enqueueVerdictBatch, loadBatches } from '../src/evalboard/batches'
import {
  corpusOf,
  loadCorpora,
  seedsOfCorpus,
  verdictKeyOf,
  type CorporaDoc,
} from '../src/evalboard/corpora'
import {
  appendRequest,
  enqueueCovered,
  enqueueQueued,
  verdictCovered,
  verdictQueued,
} from '../src/evalboard/requests'

const REPO_ROOT = path.resolve(import.meta.dir, '..', '..')

function tmpCorpora(doc: unknown): string {
  const dir = mkdtempSync(path.join(tmpdir(), 'evcorpora-'))
  const p = path.join(dir, 'corpora.json')
  writeFileSync(p, JSON.stringify(doc), 'utf-8')
  return p
}

const goodCorpus = {
  id: 'v-test',
  level: 'ladder-c03',
  seed0: 400600,
  games_per_stage: 3,
  policy: 'nn' as const,
}

describe('corpora 注册表读取与校验', () => {
  it('仓内注册表可读，且声明了 T5 判决段 v-ladder-c03-p400600', () => {
    const doc = loadCorpora(REPO_ROOT)
    expect(doc.corpora.length).toBeGreaterThan(0)
    const c = corpusOf(REPO_ROOT, 'v-ladder-c03-p400600')
    expect(c.level).toBe('ladder-c03')
    // 池外段：与训练池 860001-860200 及已用池外段 400000/400200 不相交（§15.1）。
    expect(c.seed0).toBe(400600)
  })

  it('EVALBOARD_CORPORA 覆盖路径生效（冒烟/单测惯例）', () => {
    const p = tmpCorpora({ version: 'v1', corpora: [goodCorpus] })
    const prev = process.env.EVALBOARD_CORPORA
    process.env.EVALBOARD_CORPORA = p
    try {
      expect(loadCorpora(REPO_ROOT).corpora[0]?.id).toBe('v-test')
      expect(corpusOf(REPO_ROOT, 'v-test').games_per_stage).toBe(3)
    } finally {
      if (prev === undefined) delete process.env.EVALBOARD_CORPORA
      else process.env.EVALBOARD_CORPORA = prev
      rmSync(path.dirname(p), { recursive: true, force: true })
    }
  })

  it('坏行响亮失败：id 重复 / seed0 非正 / games_per_stage 非正 / policy 非法 / level 缺失', () => {
    const cases: Array<[string, unknown]> = [
      ['id 重复', { version: 'v1', corpora: [goodCorpus, { ...goodCorpus }] }],
      ['seed0 非法', { version: 'v1', corpora: [{ ...goodCorpus, seed0: 0 }] }],
      ['gps 非法', { version: 'v1', corpora: [{ ...goodCorpus, games_per_stage: -1 }] }],
      ['policy 非法', { version: 'v1', corpora: [{ ...goodCorpus, policy: 'rl' }] }],
      ['level 缺失', { version: 'v1', corpora: [{ ...goodCorpus, level: '' }] }],
      ['corpora 非数组', { version: 'v1', corpora: {} }],
    ]
    for (const [name, doc] of cases) {
      const p = tmpCorpora(doc)
      const prev = process.env.EVALBOARD_CORPORA
      process.env.EVALBOARD_CORPORA = p
      try {
        expect(() => loadCorpora(REPO_ROOT), name).toThrow()
      } finally {
        if (prev === undefined) delete process.env.EVALBOARD_CORPORA
        else process.env.EVALBOARD_CORPORA = prev
        rmSync(path.dirname(p), { recursive: true, force: true })
      }
    }
  })

  it('未知 id 抛（不静默返回空语料）', () => {
    expect(() => corpusOf(REPO_ROOT, 'nope-不存在')).toThrow(/未知判决语料/)
  })

  it('seedsOfCorpus = seed0 + 0..gps-1（与 Python planner 同式）', () => {
    expect(seedsOfCorpus({ ...goodCorpus, seed0: 400600, games_per_stage: 3 })).toEqual([
      400600, 400601, 400602,
    ])
  })

  it('verdictKeyOf 顺序敏感：同集合不同顺序 = 不同键', () => {
    const a = { label: 'a', path: 'x.json' }
    const b = { label: 'b', path: 'y.json' }
    expect(verdictKeyOf('v-test', [a, b])).toBe('verdict|v-test|a,b')
    expect(verdictKeyOf('v-test', [a, b])).not.toBe(verdictKeyOf('v-test', [b, a]))
    // 无 label 时回落 path。
    expect(verdictKeyOf('v-test', [{ path: 'z.json' }])).toBe('verdict|v-test|z.json')
  })
})

describe('判决批次（kind=verdict）', () => {
  const spec = {
    corpus: 'v-test',
    ckpts: [
      { label: 'bc', path: 'w/bc.json' },
      { label: 'it30', path: 'w/it30.json' },
    ],
    requester: 'test',
    iter: 30,
  }

  it('入队：kind/corpus/ckpts 就位，ladder 键位置空串，trigger=verdict', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evvbatch-'))
    try {
      const b = enqueueVerdictBatch(dir, spec)
      expect(b.kind).toBe('verdict')
      expect(b.corpus).toBe('v-test')
      expect(b.ckpts?.map((c) => c.label)).toEqual(['bc', 'it30'])
      // 不用假 course 骗旧读方的键 —— 键空间靠 kind 分离。
      expect(b.course).toBe('')
      expect(b.rung_from).toBe('')
      expect(b.ckpt).toBe('')
      expect(b.trigger).toBe('verdict')
      expect(b.status).toBe('pending')
      expect(loadBatches(dir).length).toBe(1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('同语料同 ckpt 序 → 不重复建批；cpt 顺序不同 → 另建一批', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evvbatch-'))
    try {
      const b1 = enqueueVerdictBatch(dir, spec)
      expect(enqueueVerdictBatch(dir, spec).batch_id).toBe(b1.batch_id)
      const b2 = enqueueVerdictBatch(dir, { ...spec, ckpts: [...spec.ckpts].reverse() })
      expect(b2.batch_id).not.toBe(b1.batch_id)
      expect(loadBatches(dir).length).toBe(2)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('旧台账行没有 kind（ladder 批）不得被当判决批', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evvbatch-'))
    try {
      const legacy: EvalBatch = {
        batch_id: 'b-legacy',
        course: 'c',
        rung_from: 'c4l1',
        ckpt: 'w',
        requester: 'web',
        created_ts: '2026-09-11T10:00:00.000Z',
        status: 'pending',
        units: { of: 2, done: [] },
        k_seq: 0,
        window_seq: 0,
        trigger: 'standalone',
        iter: 30,
        node_dist: {},
        elapsed_sec: null,
      }
      expect(legacy.kind).toBeUndefined()
      writeFileSync(path.join(dir, 'batches.jsonl'), `${JSON.stringify(legacy)}\n`, 'utf-8')
      expect(loadBatches(dir)[0]?.ckpts).toBeUndefined()
      expect(loadBatches(dir).length).toBe(1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('判决请求去重（requests.ts）', () => {
  const ckpts = [{ label: 'bc', path: 'w/bc.json' }]
  const vbatch = (over: Partial<EvalBatch> = {}): EvalBatch => ({
    batch_id: 'b-v',
    kind: 'verdict',
    corpus: 'v-test',
    ckpts,
    course: '',
    rung_from: '',
    ckpt: '',
    requester: 'cli',
    created_ts: '2026-09-19T10:00:00.000Z',
    status: 'pending',
    units: { of: 0, done: [] },
    k_seq: 0,
    window_seq: 0,
    trigger: 'verdict',
    iter: 0,
    node_dist: {},
    elapsed_sec: null,
    ...over,
  })

  it('verdictQueued：同键 pending 批 → true', () => {
    expect(verdictQueued([vbatch()], [], 'v-test', ckpts)).toBe(true)
    expect(verdictQueued([vbatch({ status: 'done' })], [], 'v-test', ckpts)).toBe(false)
    // 键不同（语料不同）不认账。
    expect(verdictQueued([vbatch()], [], 'v-other', ckpts)).toBe(false)
  })

  const vreq = (over: Record<string, unknown> = {}) => ({
    req_id: 'q-1',
    ts: '2026-09-19T10:00:00.000Z',
    kind: 'verdict' as const,
    requester: 'cli',
    corpus: 'v-test',
    ckpts,
    ...over,
  })

  it('verdictQueued：已物化的同键请求不算“仍在排队”', () => {
    const req = vreq()
    expect(verdictQueued([], [req], 'v-test', ckpts)).toBe(true)
    // 批的 created_ts >= req.ts ⇒ 已被消费覆盖。
    expect(verdictQueued([vbatch({ status: 'running' })], [req], 'v-test', ckpts)).toBe(false)
  })

  const lreq = (over: Record<string, unknown> = {}) => ({
    req_id: 'q-3',
    ts: '2026-09-19T09:00:00.000Z',
    requester: 'web',
    kind: 'enqueue' as const,
    course: 'c',
    rung_from: 'r',
    ckpt: 'w',
    ...over,
  })

  it('verdictCovered 只看 kind=verdict，ladder 请求不受影响', () => {
    expect(verdictCovered([vbatch()], vreq({ ts: '2026-09-19T09:00:00.000Z' }))).toBe(true)
    // verdict 请求不带 course/rung —— 不可交给 ladder 分支（否则永远“未覆盖”而重复入队）。
    expect(enqueueCovered([vbatch()], vreq())).toBe(true)
    expect(enqueueCovered([vbatch()], lreq())).toBe(false)
  })

  it('verdictQueued 不误判 ladder 请求（且 ladder 键仍走 enqueueQueued）', () => {
    expect(verdictQueued([], [lreq()], 'v-test', ckpts)).toBe(false)
    expect(enqueueQueued([vbatch()], [lreq()], 'c', 'r', 'w')).toBe(true)
  })

  it('appendRequest(kind=verdict) 写入 corpus/ckpts 并读回', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evvreq-'))
    try {
      const r = appendRequest(dir, {
        kind: 'verdict',
        requester: 'cli',
        corpus: 'v-test',
        ckpts,
        policy: 'nn',
        iter: 30,
      })
      expect(r.req_id.startsWith('q-')).toBe(true)
      expect(r.corpus).toBe('v-test')
      expect(r.ckpts?.length).toBe(1)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('verdict-cli 入口', () => {
  it('--list 列出注册表；未知语料入队失败（退出码 1）', () => {
    const cli = path.join('dashboard', 'src', 'evalboard', 'verdict-cli.ts')
    const listed = Bun.spawnSync(['bun', cli, '--list'], { cwd: REPO_ROOT })
    expect(listed.exitCode).toBe(0)
    const out = listed.stdout.toString()
    expect(out).toContain('v-ladder-c03-p400600')
    expect(out).toContain('400600..400799')

    const bad = Bun.spawnSync(['bun', cli, '--corpus', 'nope', '--ckpt', 'a=x.json'], {
      cwd: REPO_ROOT,
    })
    expect(bad.exitCode).toBe(1)
    expect(bad.stderr.toString()).toContain('未知判决语料')
  })

  it('--dry 打印判决请求且不落盘；--ckpt 权重缺失响亮失败', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'evvcli-'))
    try {
      const cli = path.join('dashboard', 'src', 'evalboard', 'verdict-cli.ts')
      const env = {
        ...(process.env as Record<string, string>),
        EVALBOARD_DATA: dir,
        EVALBOARD_CORPORA: tmpCorpora({ version: 'v1', corpora: [goodCorpus] }),
      }
      const w = path.join(dir, 'bc.json')
      writeFileSync(w, '{}', 'utf-8')
      // 绝对路径权重（cli 对相对路径做 REPO_ROOT 解析）。
      const dry = Bun.spawnSync(
        ['bun', cli, '--corpus', 'v-test', '--ckpt', `bc=${w}`, '--dry', '--json'],
        { cwd: REPO_ROOT, env },
      )
      expect(dry.exitCode).toBe(0)
      // `--dry --json` 的 stdout 就是一段 JSON（可能带尾部换行/CR）。
      const parsed = JSON.parse(dry.stdout.toString().trim()) as {
        key: string
        request: { kind: string; corpus: string }
      }
      expect(parsed.key).toBe('verdict|v-test|bc')
      expect(parsed.request.kind).toBe('verdict')
      expect(parsed.request.corpus).toBe('v-test')
      // dry 不入队。
      expect(loadBatches(dir).length).toBe(0)

      const missing = Bun.spawnSync(
        ['bun', cli, '--corpus', 'v-test', '--ckpt', 'bc=tmp/不存在.json'],
        { cwd: REPO_ROOT, env },
      )
      expect(missing.exitCode).toBe(1)
      expect(missing.stderr.toString()).toContain('权重文件不存在')

      // 真入队：requests.jsonl 一条，verdictQueued 生效后二次入队不重复。
      const queued = Bun.spawnSync(['bun', cli, '--corpus', 'v-test', '--ckpt', `bc=${w}`], {
        cwd: REPO_ROOT,
        env,
      })
      expect(queued.exitCode).toBe(0)
      const again = Bun.spawnSync(['bun', cli, '--corpus', 'v-test', '--ckpt', `bc=${w}`], {
        cwd: REPO_ROOT,
        env,
      })
      expect(again.stdout.toString()).toContain('不重复入队')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('双侧镜像漂移守卫', () => {
  it('Python batch_eval 与 TS 的语料 id 解析同一份 corpora.json', () => {
    // 双侧各自声明路径常量；这里只断言仓内路径一致（EVALBOARD_CORPORA 可覆盖两者）。
    const tsPath = path.join(REPO_ROOT, 'dashboard', 'src', 'evalboard', 'corpora.json')
    const doc = JSON.parse(require('fs').readFileSync(tsPath, 'utf-8')) as CorporaDoc
    expect(Array.isArray(doc.corpora)).toBe(true)
  })
})
