import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createTabTitleSync as createKaggleTabTitleSync } from '../src/notebook-companion/sites/kaggle'
import {
  composeTabTitle,
  createTabTitleReporter,
  createTabTitleSync,
  stripTabMarks,
  TAB_MARK_RUNNING,
  TAB_MARK_SCHEDULED,
  TAB_MARK_STOPPED,
} from '../src/notebook-companion/ui/tab-title'

const BOOT = readFileSync(join(import.meta.dir, '../src/notebook-companion/app/boot.ts'), 'utf8')
const count = (text: string, re: RegExp): number => text.match(re)?.length ?? 0

describe('标签页标题：状态图标', () => {
  it('stripTabMarks：只剥离开头的图标，正文不动', () => {
    expect(stripTabMarks('⛔ rollout')).toBe('rollout')
    expect(stripTabMarks('⏰ rollout')).toBe('rollout')
    expect(stripTabMarks('▶️ rollout')).toBe('rollout')
    expect(stripTabMarks('⛔ ⏰ rollout')).toBe('rollout')
    expect(stripTabMarks('▶️ ⏰ rollout')).toBe('rollout')
    expect(stripTabMarks('⏹️ rollout')).toBe('rollout') // 旧版停止图标
    expect(stripTabMarks('⛔rollout')).toBe('rollout')
    expect(stripTabMarks('rollout ⛔')).toBe('rollout ⛔')
    expect(stripTabMarks('plain')).toBe('plain')
  })

  it('composeTabTitle：中止优先于执行中；定时图标与两者正交；空名不编标题', () => {
    expect(composeTabTitle('rollout', { stopped: false, running: false, scheduled: false })).toBe(
      'rollout',
    )
    expect(composeTabTitle('rollout', { stopped: false, running: true, scheduled: false })).toBe(
      `${TAB_MARK_RUNNING} rollout`,
    )
    expect(composeTabTitle('rollout', { stopped: true, running: true, scheduled: false })).toBe(
      `${TAB_MARK_STOPPED} rollout`,
    )
    expect(composeTabTitle('rollout', { stopped: false, running: false, scheduled: true })).toBe(
      `${TAB_MARK_SCHEDULED} rollout`,
    )
    expect(composeTabTitle('rollout', { stopped: true, running: false, scheduled: true })).toBe(
      `${TAB_MARK_STOPPED} ${TAB_MARK_SCHEDULED} rollout`,
    )
    expect(composeTabTitle('rollout', { stopped: false, running: true, scheduled: true })).toBe(
      `${TAB_MARK_RUNNING} ${TAB_MARK_SCHEDULED} rollout`,
    )
    // 已经带过图标的标题（页面重渲染/上一拍写的）不会叠成两个
    expect(
      composeTabTitle('⛔ ⏰ rollout', { stopped: false, running: true, scheduled: false }),
    ).toBe(`${TAB_MARK_RUNNING} rollout`)
    expect(composeTabTitle('', { stopped: true, running: true, scheduled: true })).toBe('')
  })

  it('reporter：执行中 / 已停 / 定时切换写标题；重连会撤销中止；页面改名继续跟随', () => {
    const doc = { title: 'rollout' }
    const sync = createTabTitleSync(doc, () => 'https://example.test/rollout')
    const tab = createTabTitleReporter(sync)
    tab.refresh()
    expect(doc.title).toBe('rollout')

    tab.setScheduled(true)
    expect(doc.title).toBe(`${TAB_MARK_SCHEDULED} rollout`)
    tab.setRunning(true)
    expect(doc.title).toBe(`${TAB_MARK_RUNNING} ${TAB_MARK_SCHEDULED} rollout`)
    tab.observeSession('stopped')
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} ${TAB_MARK_SCHEDULED} rollout`)
    tab.observeSession('unknown')
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} ${TAB_MARK_SCHEDULED} rollout`) // 认不出 ≠ 已恢复

    tab.observeSession('running')
    expect(doc.title).toBe(`${TAB_MARK_RUNNING} ${TAB_MARK_SCHEDULED} rollout`) // 重连：撤 ⛔，但 cell 还在跑
    tab.setRunning(false)
    expect(doc.title).toBe(`${TAB_MARK_SCHEDULED} rollout`)
    tab.setScheduled(false) // 定时任务跑完/删掉：⏰ 立刻撤
    expect(doc.title).toBe('rollout')

    tab.markStopped()
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} rollout`)
    tab.setScheduled(true)
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} ${TAB_MARK_SCHEDULED} rollout`)
    doc.title = 'rollout' // 页面把我们写的标题覆盖掉
    tab.refresh()
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} ${TAB_MARK_SCHEDULED} rollout`)
  })

  it('Kaggle 标题钉住：带图标时仍解析 notebook 名，不把图标吃进名字', () => {
    let href = 'https://www.kaggle.com/code/a/rollout/edit'
    const doc = { title: 'rollout | Kaggle' }
    const sync = createKaggleTabTitleSync(doc, () => href)
    sync.setStatus({ stopped: true })
    expect(sync()).toBe('rollout')
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} rollout`)

    doc.title = 'Kaggle Notebook Editor' // Kaggle 又覆写回去
    expect(sync()).toBe('rollout')
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} rollout`)

    href = 'https://www.kaggle.com/code/a/other/edit' // SPA 换 notebook
    expect(sync()).toBe('other')
    expect(doc.title).toBe(`${TAB_MARK_STOPPED} other`)
  })

  it('两个角色都接上 reporter；帧上报执行态；停会话成功后标记中止；定时任务上 ⏰', () => {
    // host（Kaggle）+ single（Colab/AI Studio/魔搭）各一套
    expect(count(BOOT, /createTabTitleReporter\(/g)).toBe(2)
    expect(
      count(BOOT, /tabTitle\.observeSession\(sessionTitleState\(\)\)/g),
    ).toBeGreaterThanOrEqual(6)
    expect(count(BOOT, /tabTitle\.markStopped\(\)/g)).toBe(2) // host stopNow + single stopNow
    expect(BOOT).toContain("bridge.note('status:cells'")
    expect(BOOT).toContain("m.name === 'status:cells'")
    expect(BOOT).toContain('tabTitle.setRunning(cellsExecuting(adapter))')
    expect(BOOT).toContain('tabTitle.refresh()')
    // 两个角色各自从本页任务表算 ⏰：面板变化时 + 每秒一拍 + 启动时
    expect(count(BOOT, /function hasScheduledTasks\(\)/g)).toBe(2)
    expect(count(BOOT, /tabTitle\.setScheduled\(hasScheduledTasks\(\)\)/g)).toBeGreaterThanOrEqual(
      4,
    )
  })
})
