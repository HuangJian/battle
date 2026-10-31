import { afterEach, describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadDom, type DomHandle } from '../happydom'

const FRAME = readFileSync(join(import.meta.dir, 'fixtures/kaggle-frame.html'), 'utf8')
const OUTER = readFileSync(join(import.meta.dir, 'fixtures/kaggle-outer.html'), 'utf8')
const COLAB = readFileSync(join(import.meta.dir, 'fixtures/colab.html'), 'utf8')
const AISTUDIO = readFileSync(join(import.meta.dir, 'fixtures/aistudio.html'), 'utf8')

const opened: DomHandle[] = []
function dom(html: string, url?: string): DomHandle {
  const w = loadDom(html, url)
  opened.push(w)
  return w
}

afterEach(() => {
  for (const w of opened.splice(0)) w.close()
})

describe('Kaggle 夹具（结构忠实优先于最小）', () => {
  it('frame：3 个 cell、uuid 唯一、滚动容器/折叠器齐全', () => {
    const d = dom(FRAME).document
    expect(d.querySelectorAll('.jp-Cell').length).toBe(3)
    expect(d.querySelectorAll('.jp-CodeCell').length).toBe(2)
    const uuids = [...d.querySelectorAll('.jp-Cell')].map((c) => c.getAttribute('data-uuid'))
    expect(new Set(uuids).size).toBe(3)
    expect(d.querySelector('.jp-WindowedPanel-outer')).not.toBeNull()
    expect(d.querySelectorAll('.jp-InputCollapser').length).toBe(2)
    expect(d.querySelectorAll('.jp-OutputCollapser').length).toBe(2)
    expect(d.querySelectorAll('.jp-cell-menu.jp-cell-toolbar').length).toBe(1)
  })

  it('frame：stdout 与 stderr 都在，CRLF/孤立 CR/无换行相邻都保留', () => {
    const d = dom(FRAME).document
    const stdout =
      d.querySelector('[data-mime-type="application/vnd.jupyter.stdout"] pre')?.textContent ?? ''
    const stderr =
      d.querySelector('[data-mime-type="application/vnd.jupyter.stderr"] pre')?.textContent ?? ''
    expect(stdout).toContain('\r\n')
    expect(stdout).toContain('alive (0 min)[06:23:09]') // 两条日志之间没有换行
    expect(stdout).toContain('\r') // tqdm 的孤立 CR
    expect(stderr).toContain('RuntimeError: boom')
  })

  it('run 只有一份、在笔记本工具栏的隐藏 popup 里，且不在任何 cell 内', () => {
    const d = dom(FRAME).document
    const run = [...d.querySelectorAll('[data-command="runmenu:run"]')]
    expect(run.length).toBe(1)
    expect(run[0]!.closest('.jp-Cell')).toBeNull()
    expect(run[0]!.closest('.jp-Toolbar-responsive-popup.lm-mod-hidden')).not.toBeNull()
    // notebook toolbar 的 collapse-all 是标题折叠，不是折叠全部代码
    expect(d.querySelector('[data-command="toc:toggle-collapse"]')).not.toBeNull()

    const runCell = d.querySelector('[data-command="notebook:run-cell"]')
    expect(runCell?.tagName).toBe('LI')
    expect(runCell?.closest('.jp-Cell')).toBeNull()
  })

  it('命令面板铺好 >=8 条命令（clear-all 与 hide-all 都在）', () => {
    const d = dom(FRAME).document
    const ids = [...d.querySelectorAll('li[data-command]')].map((li) =>
      li.getAttribute('data-command'),
    )
    expect(ids.length).toBeGreaterThanOrEqual(8)
    expect(ids).toContain('notebook:clear-all-cell-outputs')
    expect(ids).toContain('notebook:clear-cell-output')
    expect(ids).toContain('notebook:hide-all-cell-code')
  })

  it('outer：iframe / Stop session / 状态文案；URL 已脱敏（无 JWT）', () => {
    const d = dom(OUTER, 'https://www.kaggle.com/code/example/rollout/edit/run/123').document
    const iframe = d.querySelector('iframe[name="notebook-editor-cells"]')
    expect(iframe).not.toBeNull()
    expect(iframe?.getAttribute('src')).toContain('kkb-production.jupyter-proxy.kaggle.net')
    expect(d.querySelector('[aria-label="Stop session"]')).not.toBeNull()
    expect(d.body.textContent ?? '').toContain('Draft Session')
    expect(OUTER).not.toMatch(/eyJ[A-Za-z0-9_-]{8,}\./)
    expect(OUTER).not.toContain('session=eyJ')
    expect(iframe?.getAttribute('src')).not.toMatch(/eyJ[A-Za-z0-9_-]{8,}/)
  })

  it('AI Studio：JupyterLab 分支面板、Monaco 行、输出区、折叠器、命令项', () => {
    const d = dom(AISTUDIO).document
    expect(d.querySelector('.jp-NotebookPanel')).not.toBeNull()
    expect(d.querySelectorAll('.jp-Cell').length).toBe(6)
    expect(d.querySelectorAll('.jp-CodeCell').length).toBe(5)
    expect(d.querySelectorAll('.jp-Cell .view-line').length).toBeGreaterThan(0)
    expect(d.querySelectorAll('.jp-OutputArea-child').length).toBeGreaterThan(0)
    expect(d.querySelectorAll('.jp-InputCollapser').length).toBeGreaterThan(0)
    expect(d.querySelector('.jp-Notebook-ExecutionIndicator')).not.toBeNull()
    for (const id of [
      'notebook:run-cell',
      'notebook:run-all-cells',
      'notebook:clear-cell-output',
      'notebook:show-all-cell-code',
    ]) {
      expect(d.querySelector(`[data-command="${id}"]`)).not.toBeNull()
    }
    // 输出已脱敏：真实存档里的训练日志绝不入库
    expect(AISTUDIO).not.toContain('opt 身份命中')
  })

  it('四份夹具都带来源/日期注记，且不含任何 token（脱敏不靠人工复查）', () => {
    // 注释要求「来源 + 提取/入库日期」（plan §5-W0）；文档化在头部，测试把它变成约束。
    for (const html of [FRAME, OUTER, COLAB, AISTUDIO]) {
      expect(html).toContain('夹具（脱敏）')
      expect(html).toMatch(/来源：2026-10-\d{2}/)
      expect(html).toMatch(/mhtml 存档/)
      expect(html).not.toMatch(/eyJ[A-Za-z0-9_-]{8,}\./) // JWT 的 header.payload 形态
      expect(html).not.toMatch(/gh[pousr]_[A-Za-z0-9]{20,}/) // GitHub token
      expect(html).not.toMatch(/Bearer\s+[A-Za-z0-9._-]{20,}/)
      expect(html).not.toMatch(/(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[A-Za-z0-9]{16,}/i)
      expect(html).not.toContain('authKey=') // 原存档日志里有采样节点的 authKey
    }
    // 占位只表明 URL 形状（带 query），不冒充真会话：不含 JWT 前缀。
    expect(OUTER).toContain('SYNTHETIC-NOT-A-TOKEN')
  })
})
