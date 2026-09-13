/**
 * web-log-view.test.ts — §367 日志结构化解析（parseLogLine / parsePhaseFromLog / parseTrainingEvent）+ 事件卡
 *
 * 分层：src/web/view/log-view.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { renderLogPage } from '../src/web/render'
import { formatBytes, parseLogLine, parseTrainingEvent } from '../src/web/view'

describe('日志页展示层（§367：结构化解析 + 事件卡）', () => {
  it('parseLogLine：切分时间戳 + [组件] 标签 + 正文，按关键词判级别', () => {
    const l = parseLogLine('[13:49:32] [sampler-agent] task done key=abc buf.len=3980')
    expect(l.ts).toBe('13:49:32')
    expect(l.tag).toBe('sampler-agent')
    expect(l.level).toBe('info')
    expect(l.text).toBe('task done key=abc buf.len=3980')
    // 错误/警告级别
    expect(parseLogLine('[10:00:00] task failed: timeout').level).toBe('error')
    expect(parseLogLine('[10:00:01] retrying...').level).toBe('warn')
    expect(parseLogLine('plain line without prefix').ts).toBeNull()
    expect(parseLogLine('plain line without prefix').tag).toBeNull()
    expect(parseLogLine('plain line without prefix').level).toBe('info')
  })

  it('parseTrainingEvent：trainingLoop JSON 行 → event 徽章 + 精选字段；iter_error 带 error', () => {
    const it = JSON.stringify({
      event: 'iteration',
      iter: 7,
      winRate: 0.2933,
      kl: 0.004847568204240764,
      time: '2026-09-08 06:44:04',
      expectedGames: 150,
    })
    const e = parseTrainingEvent(it)
    expect(e).not.toBeNull()
    expect(e!.event).toBe('iteration')
    expect(e!.fields.find(([k]) => k === 'it')?.[1]).toBe('7')
    expect(e!.fields.find(([k]) => k === 'win')?.[1]).toBe('29.3%')
    expect(e!.fields.find(([k]) => k === 'kl')?.[1]).toBe('0.0048')
    expect(e!.hasError).toBeUndefined()

    const err = parseTrainingEvent(
      JSON.stringify({
        event: 'iter_error',
        iter: 13,
        error: 'HubClientError: wait_job: 超时（>1800.0s）未完成',
        time: '2026-09-08 08:24:31',
      }),
    )
    expect(err!.event).toBe('iter_error')
    expect(err!.hasError).toContain('超时')
    // 非 JSON / 无 event 字段 → null
    expect(parseTrainingEvent('plain text')).toBeNull()
    expect(parseTrainingEvent(JSON.stringify({ foo: 1 }))).toBeNull()
  })

  it('formatBytes 人性化：B/KB/MB', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.00 MB')
    expect(formatBytes(-1)).toBe('—')
  })

  it('renderLogPage：trainingLoop JSON 行渲染为结构化事件卡（event 徽章 + 字段），普通行带行号/时间戳列', () => {
    const p = {
      component: 'trainingLoop',
      label: 'trainingLoop (训练循环)',
      log: 'tmp/x/training_log.jsonl',
      exists: true,
      fileSize: 1024,
      lines: [
        JSON.stringify({ event: 'iteration', iter: 7, winRate: 0.2933, time: 't' }),
        '[13:49:32] [sampler-agent] task done key=abc',
        'a failed thing',
      ],
      truncated: false,
    }
    const html = renderLogPage(p, { components: [], follow: true, lines: 20 })
    expect(html).toContain('tc-logline--ev-iteration')
    expect(html).toContain('iteration')
    expect(html).toContain('tc-logline--error') // failed 行红色级别
    expect(html).toContain('tc-logline__no') // 行号 gutter
    expect(html).not.toContain('<script>alert')
  })

  it('renderLogPage（§371）：尾行 all 选项 + 常驻直达底部 FAB + 更新于指示 + all 截断文案', () => {
    const p = {
      component: 'selfNode',
      label: 'selfNode',
      log: 'tmp/sampler-agent.log',
      exists: true,
      fileSize: 888,
      lines: ['[13:49:32] [sampler-agent] task done', 'a doomed thing'],
      truncated: true,
      updatedAt: 1788820000000,
    }
    const html = renderLogPage(p, { components: [], follow: false, lines: 'all' })
    expect(html).toContain('value="all"') // 尾行下拉含 all
    expect(html).toContain('已截断·尾部窗口') // all 专用截断文案
    expect(html).toContain('更新于') // 取数时刻可感知（自动刷新=有动）
    expect(html).toContain('tc-logbtn') // 直达底部按钮并入工具栏（不再浮动 FAB）
    expect(html).toContain('已到底部') // 初始贴底态文案
    expect(html).not.toContain('<script>alert')
  })

  it('renderLogPage（§372）：共 N 行 = 文件总行数（totalLines）', () => {
    const p = {
      component: 'selfNode',
      label: 'selfNode',
      log: 'tmp/sampler-agent.log',
      exists: true,
      fileSize: 888,
      lines: Array.from({ length: 5 }, (_, i) => `line-${i}`),
      truncated: true,
      totalLines: 1234,
    }
    const html = renderLogPage(p, { components: [], follow: false, lines: 200 })
    expect(html).toMatch(/共\s*<b>1234<\/b>\s*行/) // 顶部显示文件总行数而非截断窗口 5
    expect(html).not.toMatch(/共\s*<b>5<\/b>\s*行/)
  })
})
