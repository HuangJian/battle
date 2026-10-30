import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'
import { collectProbe, probeLine, stripUrl } from '../src/notebook-companion/probe'
import { StubFrameAdapter, StubSessionAdapter } from '../src/notebook-companion/sites/stub'

const FRAME = readFileSync(join(import.meta.dir, 'fixtures/kaggle-frame.html'), 'utf8')
const opened: DomHandle[] = []
afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

describe('探测模式：只读事实，URL 绝不带 query', () => {
  it('stripUrl 去掉 JWT query/hash', () => {
    expect(stripUrl('https://x.test/a/b?session=SECRET#frag')).toBe('https://x.test/a/b')
    expect(stripUrl('not a url')).toBe('')
  })

  it('采集夹具：cells/code/output/滚动容器/命令都在', () => {
    const w = loadDom(FRAME)
    opened.push(w)
    const r = collectProbe(
      w.document as unknown as Document,
      'https://kkb-production.jupyter-proxy.kaggle.net/x?session=SECRET',
      1,
    )
    expect(r.role).toBe('frame')
    expect(r.ready).toBe(true)
    expect(r.cells).toBe(3)
    expect(r.codeCells).toBe(2)
    expect(r.hasScrollOuter).toBe(true)
    expect(r.hasCommandMenu).toBe(true)
    expect(r.href).not.toContain('SECRET')
    expect(probeLine(r)).not.toContain('SECRET')
  })

  it('空文档/null 不抛（导航中也能安全上报）', () => {
    expect(collectProbe(null, 'https://www.kaggle.com/code/a/b/edit?x=1').cells).toBe(0)
  })

  it('Colab/AI Studio 骨架：每个命令都明确未实现（不假装成功）', async () => {
    for (const site of ['colab', 'aistudio'] as const) {
      const a = new StubFrameAdapter(site)
      expect(a.cells()).toEqual([])
      expect((await a.command('clear')).ok).toBe(false)
      expect(a.scroll('top').ok).toBe(false)
      expect(a.ready).toBe(false)
      const s = new StubSessionAdapter(site)
      expect(s.sessionState()).toBe('unknown')
      expect((await s.stopSession()).ok).toBe(false)
      const miss = await a.command('x')
      expect(miss.ok).toBe(false)
      if (!miss.ok) expect(miss.reason.length).toBeGreaterThan(0)
    }
  })
})
