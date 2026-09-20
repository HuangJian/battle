/**
 * web-component-roles.test.ts — 服务角色名（页面显示名）与用途释义（悬停）。用户指令 2026-09-20。
 *
 * 分层：src/web/view/component-roles.ts（纯数据 + 纯函数）
 *
 * 三条纪律（这个文件的三个断言组）：
 *  ① **覆盖性**：`COMPONENT_ROLES` 的键集必须与 `core/types.Component` 的联合**逐字相同**——
 *     少一个 = 那个服务在页面上显示成机器 key（`hubServer`），多一个 = 悬空词条；
 *     两个方向都要红，否则新增组件时可以「默默没有名字」。
 *  ② **名字/释义都是非空且含 key**：名字是给眼睛的，key 是给对账的（日志/URL/账本）；
 *     释义里必须出现 key，否则「这行到底对应哪个进程」在页面上无处可查。
 *  ③ **回落不是崩溃**：未知 key 回落 key 本身（不许变成空白行），用途回落空串
 *     （调用方据此不出 title，而不是挂一个空提示）。
 */

import { describe, expect, it } from 'bun:test'
import type { Component } from '../src/core/types'
import { COMPONENT_ROLES, componentHover, componentName, componentPurpose } from '../src/web/view'

/** 用户 2026-09-20 给定的映射（**逐字**：改一个字的显示名 = 故意改这里）。 */
const USER_NAMES: Record<Component, string> = {
  trainingLoop: '管事',
  hubServer: '门房',
  selfNode: '采办',
  cloudflared: '跑腿',
  localWorker: '丹徒',
}

describe('COMPONENT_ROLES（服务角色表）', () => {
  it('键集与 core 的 Component 联合逐字相同（不多不少——漏一个即页面上露出机器 key）', () => {
    const fromCore = (Object.keys(USER_NAMES) as Component[]).sort()
    const fromMap = Object.keys(COMPONENT_ROLES).sort()
    expect(fromMap).toEqual(fromCore)
  })

  it('名字 = 用户给定的角色名（逐字）；每个服务都有非空用途', () => {
    for (const key of Object.keys(USER_NAMES) as Component[]) {
      expect(componentName(key), key).toBe(USER_NAMES[key])
      expect(componentPurpose(key).length, key).toBeGreaterThan(20)
    }
  })

  it('用途里必须出现机器 key —— 悬停是 key 在上屏面的唯一落脚处', () => {
    for (const key of Object.keys(USER_NAMES) as Component[]) {
      expect(componentPurpose(key), key).toContain(key)
    }
  })

  it('悬停文案 = `角色名（key）—— 用途`（两个问题一句话说全）', () => {
    expect(componentHover('trainingLoop')).toBe(
      `管事（trainingLoop）—— ${componentPurpose('trainingLoop')}`,
    )
    expect(componentHover('trainingLoop')).toContain('训练循环本体')
    // 用途是自足整句（不是「同上」式半句）：每句都以句号收尾
    for (const key of Object.keys(USER_NAMES) as Component[]) {
      expect(componentPurpose(key).trim().endsWith('。'), key).toBe(true)
    }
  })

  it('未知 key：名字回落 key 本身（行不空白）、用途与悬停回落空串（不出空 title）', () => {
    expect(componentName('someCourseThing')).toBe('someCourseThing')
    expect(componentPurpose('someCourseThing')).toBe('')
    expect(componentHover('someCourseThing')).toBe('')
  })
})
