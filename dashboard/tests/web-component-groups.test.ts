/**
 * web-component-groups.test.ts — 组件卡分族（R3-3：服务面单例角色 vs 课程面按课程）
 *
 * 分层：src/web/view/component-groups.ts（纯函数）+ 与 `core/registry` / `server/api/component-meta`
 * 的**对拍**（判据只允许有一份）。
 *
 * 这一层要守的不是「渲染得像不像」，而是三条会让组件从 UI 上**消失**的失效模式：
 *   ① 族名单与账本槽位规则漂开（hub 变成「按课程」→ 操作员以为要给每门课各起一个 hub）；
 *   ② 新增组件忘了归档（面板照旧渲染，但它落在谁都不认识的桶里）；
 *   ③ 面板自己 filter 掉某个组件（读代码的人只看到一行 filter、不知所为何来）。
 * 三条都在这里用「全组件恰好归属一处」与「与 registry 对拍」两把尺子钉住。
 */

import { describe, expect, it } from 'bun:test'
import { componentScope } from '../src/core/registry'
import type { Component } from '../src/core/types'
import { ALL_COMPONENTS } from '../src/server/api/component-meta'
import { NODE_FACE_COMPONENTS, cardFamilies, scopeBadge } from '../src/web/view'
import type { ComponentScope, ComponentView } from '../src/web/view'

/** 组件视图最小件（只填分族需要的字段）。 */
function cv(key: string, scope?: ComponentScope): ComponentView {
  return {
    key,
    label: key,
    status: 'stopped',
    pid: null,
    url: null,
    course: null,
    mode: null,
    healthy: null,
    log: null,
    logTail: [],
    busy: false,
    ...(scope ? { scope } : {}),
  }
}

const KEYS: readonly Component[] = ALL_COMPONENTS

describe('cardFamilies：服务面 vs 课程面', () => {
  it('分族与族内顺序：服务面（agent → hub → 隧道 → trainer → 本机 worker）在前，课程面在后', () => {
    // 输入乱序（真快照的顺序不保证）：顺序必须由分组算出来，不是渲染顺序碰巧。
    // 课程面用一个**合成键**（当前没有按课程的卡片组件了，但机制必须仍然能用——
    // 它是 scope 的函数，日后真出现按课程的卡片会自然落进去）。
    const groups = cardFamilies([
      cv('localWorker', 'shared'),
      cv('cloudflared', 'shared'),
      cv('workerServe', 'course'),
      cv('someCourseThing', 'course'),
      cv('trainingLoop', 'shared'),
      cv('hubServer', 'shared'),
      cv('selfNode', 'singleton'),
    ])
    expect(groups.map((g) => g.id)).toEqual(['service', 'course'])
    expect(groups[0]!.rows.map((r) => r.key)).toEqual([
      'selfNode',
      'hubServer',
      'cloudflared',
      'trainingLoop',
      'localWorker',
    ])
    expect(groups[1]!.rows.map((r) => r.key)).toEqual(['someCourseThing'])
    // 组标题/说明上屏（分组这件事本身要看得见，不能只靠间距）
    expect(groups[0]!.title).toContain('服务面')
    expect(groups[0]!.hint).toContain('与课程数量无关')
    expect(groups[1]!.title).toContain('课程面')
  })

  it('当前真实 scope 下只有服务面一族（账本键收敛的终点，不是坏了）', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    expect(groups.map((g) => g.id)).toEqual(['service'])
    expect(groups[0]!.rows.map((r) => r.key)).toEqual([
      'selfNode',
      'hubServer',
      'cloudflared',
      'trainingLoop',
      'localWorker',
    ])
  })

  it('节点面组件（worker_server）不进卡片行——但必须**显式声明**，不是面板里的一行 filter', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    const keys = groups.flatMap((g) => g.rows.map((r) => r.key))
    expect(keys).not.toContain('workerServe')
    expect(NODE_FACE_COMPONENTS).toContain('workerServe')
    // 例外名单本身也要是真的组件（打错字 = 静默过滤掉一个真组件）
    for (const k of NODE_FACE_COMPONENTS) expect(KEYS as readonly string[]).toContain(k)
  })

  it('★ 全组件恰好归属一处：新增组件忘了归档会红（否则它会从 UI 上静默消失）', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    const placed: string[] = groups.flatMap((g) => g.rows.map((r) => r.key))
    for (const k of KEYS) {
      const where = placed.includes(k)
        ? '卡片行'
        : NODE_FACE_COMPONENTS.includes(k)
          ? '节点行'
          : '未归属'
      expect(where, `${k} 既不在卡片行也不在节点行例外名单里`).not.toBe('未归属')
    }
    // 不重复：同一组件不许出现在两族里（两处都能启停 = 两个真相）
    expect(new Set(placed).size).toBe(placed.length)
  })

  it('★ 与账本槽位规则对拍：族归属只能是 scope 的函数（两份判据不许漂）', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    for (const g of groups) {
      for (const r of g.rows) {
        const scope = componentScope(r.key as Component)
        if (g.id === 'service') expect(['singleton', 'shared'], r.key).toContain(scope)
        else expect(scope, r.key).toBe('course')
      }
    }
  })

  it('scope 缺省/未知 ⇒ 课程面（单侧保守：少一个徽章只是少信息，空贴「共享」是假承诺）', () => {
    // 缺省 scope 的 key（旧服务端 / 新增组件还没填）必须落**课程面**：它是保守的那一侧
    // （共享/单例徽章会宣称「停它就是停全局」，而按课程只会少说）
    const groups = cardFamilies([cv('hubServer'), cv('someNewThing'), cv('workerServe')])
    expect(groups.map((g) => g.id)).toEqual(['course'])
    // workerServe 不在这份名单里：它在节点行渲染（NODE_FACE_COMPONENTS，另一把尺子在上一组）
    expect(groups[0]!.rows.map((r) => r.key).sort()).toEqual(['hubServer', 'someNewThing'])
    expect(scopeBadge(cv('hubServer'))).toBeNull()
  })

  it('未列进化妆顺序的 key 落组尾但**不丢**（成员资格只由 scope 决定）', () => {
    const groups = cardFamilies([cv('someNewThing', 'course'), cv('someCourseThing', 'course')])
    expect(groups[0]!.rows.map((r) => r.key).sort()).toEqual(['someCourseThing', 'someNewThing'])
    // 顺序是化妆（ORDER 里没有它们 ⇒ 同为组尾，稳定排序保持输入序）
    expect(groups[0]!.rows.map((r) => r.key)).toEqual(['someNewThing', 'someCourseThing'])
  })

  it('空组不渲染（没东西可说时不留空壳）；只有服务面组件时只有一族', () => {
    expect(cardFamilies([])).toEqual([])
    const only = cardFamilies([cv('selfNode', 'singleton'), cv('hubServer', 'shared')])
    expect(only.map((g) => g.id)).toEqual(['service'])
  })

  it('不改动调用方数组（排序是拷贝——原地 sort 会让上游的快照顺序随渲染变化）', () => {
    const input = [cv('localWorker', 'shared'), cv('selfNode', 'singleton')]
    cardFamilies(input)
    expect(input.map((c) => c.key)).toEqual(['localWorker', 'selfNode'])
  })
})

describe('scopeBadge：只说 scope 说不出来的那件事', () => {
  it('shared ⇒ 共享（一个进程服务所有课程）', () => {
    const b = scopeBadge(cv('hubServer', 'shared'))!
    expect(b.text).toBe('共享')
    expect(b.cls).toBe('tc-cc__scope--shared')
    expect(b.title).toContain('所有并行课程')
  })

  it('singleton ⇒ 单例（全机一份）', () => {
    const b = scopeBadge(cv('selfNode', 'singleton'))!
    expect(b.text).toBe('单例')
    expect(b.cls).toBe('tc-cc__scope--singleton')
  })

  it('course ⇒ 不挂徽章（按课程是默认语义，组标题已说；每行再挂一个只是噪声）', () => {
    expect(scopeBadge(cv('localWorker', 'course'))).toBeNull()
    expect(scopeBadge(cv('workerServe', 'course'))).toBeNull()
  })

  it('★ trainer 是 shared（R3-5）：挂在共享徽章上，不是「按课程」', () => {
    expect(componentScope('trainingLoop')).toBe('shared')
    const b = scopeBadge(cv('trainingLoop', componentScope('trainingLoop')))!
    expect(b.text).toBe('共享')
    expect(b.title).toContain('所有并行课程')
  })
})
