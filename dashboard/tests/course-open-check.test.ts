/**
 * course-open-check.test.ts — 开课前的课程配置体检（2026-11-10 用户口径：
 * 「开课时候……立即响亮报错，不要等 ppo 时才错误退出」/「还要校验所有课程配置项目」）。
 *
 *  分层：判据**只在 python**（课程文件 pydantic schema + `course_openable` 运行时链 +
 *  `read_weights_meta` 权重清单）；本套件只钉「TS 侧不重写判据、只解析结论」与
 *  「基础权重选哪一份」这两件事。
 */

import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { baseWeightsPath, parseOpenCheckOutput } from '../src/stack/course-open-check'

describe('parseOpenCheckOutput：python 结论 → 结构化', () => {
  it('只认带标记的一行（stdout 混着 log 告警也不影响）', () => {
    const stdout = [
      '[run_rl] rl-config 告警：未知键 x',
      '__OPENCHECK__{"ok": true, "reason": "", "weights": null}',
    ].join('\n')
    expect(parseOpenCheckOutput(stdout)).toEqual({ ok: true, reason: '', weights: null })
  })

  it('权重失败原样带出（path + 原因）', () => {
    const r = parseOpenCheckOutput(
      '__OPENCHECK__{"ok": true, "reason": "", "weights": {"path": "tmp/c/weights.json", ' +
        '"ok": false, "reason": "ValueError: schema_major=6 ≠ 当前 7"}}',
    )
    expect(r.weights).toEqual({
      path: 'tmp/c/weights.json',
      ok: false,
      reason: 'ValueError: schema_major=6 ≠ 当前 7',
    })
  })

  it('没有结论行 / 结论形状不对 ⇒ 抛（不能把「脚本没跑到」读成通过）', () => {
    expect(() => parseOpenCheckOutput('')).toThrow(/没有输出结论行/)
    expect(() => parseOpenCheckOutput('__OPENCHECK__{"ok": "yes"}')).toThrow(/形状不对/)
  })
})

describe('baseWeightsPath：开课时体检的那份基础权重', () => {
  it('活动权重 > 封存起点 > 课程声明的 BC 种子（= 播种顺序）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-opencheck-'))
    const prev = process.env.BCITY_TMP_LOGS_DIR
    process.env.BCITY_TMP_LOGS_DIR = dir
    try {
      const active = path.join(dir, 'c1', 'weights.json')
      const seed = path.join(dir, 'seed.json')
      // ① 都没有 ⇒ BC 种子（播种那一步的输入）
      expect(baseWeightsPath('c1', undefined, '/bc/seed.json')).toBe('/bc/seed.json')
      // ② 有封存起点 ⇒ 体检起点（不是 BC）
      writeFileSync(seed, '{}')
      expect(baseWeightsPath('c1', seed, '/bc/seed.json')).toBe(seed)
      // ③ 活动权重已在盘上 ⇒ 体检它（开课不会再播种）
      mkdirSync(path.dirname(active), { recursive: true })
      writeFileSync(active, '{}')
      expect(baseWeightsPath('c1', seed, '/bc/seed.json')).toBe(active)
    } finally {
      if (prev === undefined) delete process.env.BCITY_TMP_LOGS_DIR
      else process.env.BCITY_TMP_LOGS_DIR = prev
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('接线：判据只在 python（TS 不重写课程 schema）', () => {
  const read = (p: string): string => readFileSync(p, 'utf8')

  it('探测脚本调训练栈同一条链：课程文件 → 运行时链 → 权重清单', () => {
    const src = read('src/stack/course-open-check.ts')
    for (const needle of [
      'biz.course_resolve',
      'load_course',
      'worker.course_args',
      'course_openable',
      'read_weights_meta',
      'ensure_standard_weights',
    ]) {
      expect(src).toContain(needle)
    }
  })

  it('开课在**写面之前**体检，拒绝时明说零副作用', () => {
    const src = read('src/server/actions/course-lifecycle.ts')
    const probeAt = src.indexOf('const probe = deps.probe ?? defaultProbe')
    const writeAt = src.indexOf('const prepared = prepareCourseForOpen')
    expect(probeAt).toBeGreaterThan(0)
    expect(writeAt).toBeGreaterThan(probeAt)
    expect(src).toContain('开课前配置体检不通过')
    expect(src).toContain('未写任何东西')
  })
})
