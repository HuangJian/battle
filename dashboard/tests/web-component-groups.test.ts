/**
 * web-component-groups.test.ts — 组件卡分族（R3-3：服务面单例角色 vs 课程面按课程）
 *
 * 分层：src/web/view/component-groups.ts（纯函数）+ 与 `core/registry` / `server/api/component-meta`
 * 的**对拍**（判据只允许有一份）。
 *
 * 这一层要守的不是「渲染得像不像」，而是两条会让组件从 UI 上**消失**的失效模式：
 *   ① 族名单与账本槽位规则漂开（hub 变成「按课程」→ 操作员以为要给每门课各起一个 hub）；
 *   ② 新增组件忘了归档（面板照旧渲染，但它落在谁都不认识的桶里）。
 * 两条都在这里用「全组件恰好归属一处」与「与 registry 对拍」两把尺子钉住。
 *
 * （旧第三条「面板自己 filter 掉某个组件」随 2026-09-19 的 `NODE_FACE_COMPONENTS` 删除而消失：
 * 受管组件全集 = 卡行全集，不再有例外名单。）
 *
 * 2026-09-20：逐行作用域徽章（`scopeBadge` ⇒ 「共享」/「单例」）删除（用户指令）——
 * 本文件对应的 describe 从「徽章文案」改成「**不再有**徽章 + 族标题承担这件事」：
 * 删除也需要回归闸，否则下一个人很容易把它当「漏了的功能」加回来。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { componentScope } from '../src/core/registry'
import type { Component } from '../src/core/types'
import { ALL_COMPONENTS } from '../src/server/api/component-meta'
import { cardFamilies } from '../src/web/view'
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
  it('分族与族内顺序：服务（trainer → hub → agent → 隧道 → 本机 worker）在前，课程在后', () => {
    // 输入乱序（真快照的顺序不保证）：顺序必须由分组算出来，不是渲染顺序碰巧。
    // 课程面用一个**合成键**（当前没有按课程的卡片组件了，但机制必须仍然能用——
    // 它是 scope 的函数，日后真出现按课程的卡片会自然落进去）。
    const groups = cardFamilies([
      cv('localWorker', 'shared'),
      cv('cloudflared', 'shared'),
      cv('someCourseThing', 'course'),
      cv('trainingLoop', 'shared'),
      cv('hubServer', 'shared'),
      cv('selfNode', 'singleton'),
    ])
    expect(groups.map((g) => g.id)).toEqual(['service', 'course'])
    // ★ 顺序 = 用户指令 2026-09-20：trainer（训练在跑的那个）→ hub → agent → 隧道 → 本机 worker
    expect(groups[0]!.rows.map((r) => r.key)).toEqual([
      'trainingLoop',
      'hubServer',
      'selfNode',
      'cloudflared',
      'localWorker',
    ])
    expect(groups[1]!.rows.map((r) => r.key)).toEqual(['someCourseThing'])
    // 组标题/说明上屏（分组这件事本身要看得见，不能只靠间距）——★ 标题**不带作用域后缀**
    // （2026-09-20 用户指令：「服务面 · 单例」→「服务」；逐行徐章也一并删）
    expect(groups[0]!.title).toBe('服务')
    expect(groups[0]!.hint).toContain('与课程数量无关')
    expect(groups[1]!.title).toBe('课程')
  })

  it('当前真实 scope 下只有服务一族（账本键收敛的终点，不是坏了）', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    expect(groups.map((g) => g.id)).toEqual(['service'])
    expect(groups[0]!.rows.map((r) => r.key)).toEqual([
      'trainingLoop',
      'hubServer',
      'selfNode',
      'cloudflared',
      'localWorker',
    ])
  })

  it('★ 全组件恰好归属一处：新增组件忘了归档会红（否则它会从 UI 上静默消失）', () => {
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    const placed: string[] = groups.flatMap((g) => g.rows.map((r) => r.key))
    // 受管组件全集 = 卡行全集（自 2026-09-19 起没有例外名单：本机伪节点退出受管组件）
    for (const k of KEYS) expect(placed, `${k} 没有任何一族收它`).toContain(k)
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

  it('scope 缺省/未知 ⇒ 课程面（单侧保守：把行放进另一族只是少说，冒充共享是假承诺）', () => {
    // 缺省 scope 的 key（旧服务端 / 新增组件还没填）必须落**课程面**：它是保守的那一侧
    // （归进服务面会读成「停它就是停全局」）
    const groups = cardFamilies([cv('hubServer'), cv('someNewThing')])
    expect(groups.map((g) => g.id)).toEqual(['course'])
    expect(groups[0]!.rows.map((r) => r.key).sort()).toEqual(['hubServer', 'someNewThing'])
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

// ────────────────────────── 作用域**不**逐行上徽章（2026-09-20） ──────────────────────────

describe('作用域只在族标题上说一次（逐行「共享/单例」徽章已下线）', () => {
  it('★ scopeBadge 不再导出：标签删掉，分组判据留住', () => {
    // 存在性断言（不是「读某个文件里没有那几个字」）：函数若被加回来，面板就有两条路
    // 重新长出「每行重复一遍族作用域」的噪声。
    const view = readFileSync(
      join(DASHBOARD_ROOT, 'src', 'web', 'view', 'component-groups.ts'),
      'utf-8',
    )
    expect(view).not.toContain('export function scopeBadge')
    // 分组仍由 scope 决定（删的是标签，不是判据）
    const groups = cardFamilies(KEYS.map((k) => cv(k, componentScope(k))))
    expect(groups[0]!.rows.map((r) => r.key)).toContain('selfNode') // singleton
    expect(groups[0]!.rows.map((r) => r.key)).toContain('hubServer') // shared
  })

  it('★ 面板不再消费它，样式表里也不再有它的规则（三者一起才能钉住）', () => {
    const cards = readFileSync(
      join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'ComponentCards.tsx'),
      'utf-8',
    )
    expect(cards).not.toContain('scopeBadge')
    const css = readFileSync(join(DASHBOARD_ROOT, 'src', 'web', 'theme.css'), 'utf-8')
    expect(/^\.tc-cc__scope/m.test(css)).toBe(false)
  })

  it('★ trainer 仍是 shared（R3-5）：它在服务面族里，不是「按课程」', () => {
    // 徽章没了，但「trainer 是共享进程」这个**事实**必须仍然被表达（否则操作员会以为
    // 「给这门课再起一个 trainer」是合法的）——它现在由族归属承担。
    expect(componentScope('trainingLoop')).toBe('shared')
    const groups = cardFamilies([cv('trainingLoop', componentScope('trainingLoop'))])
    expect(groups.map((g) => g.id)).toEqual(['service'])
    // 族说明用**页面上同一套角色名**（2026-09-20 用户指令）——标题与行名不得各说一套
    expect(groups[0]!.hint).toContain('管事（trainer）')
    // 作用域事实仍要看得见（只不在标题/行里重复）：它写在族说明里
    expect(groups[0]!.hint).toContain('各一个进程服务所有课程')
  })
})
