import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Gate 0 是**人工闸**，agent 不开浏览器（AGENTS §5），所以它的物料不能靠「人记得」：
 * 探针少一条 `@match`、被加上 `@noframes`、或清单丢掉一条判据，都会让那次 10 分钟的验证
 * 变成一次**假通过**。这里钉住物料的形状（不执行探针本身）。
 */
const GATE0_DIR = join(import.meta.dir, '..', 'gate0')
const probe = readFileSync(join(GATE0_DIR, 'probe.user.js'), 'utf8')
const checklist = readFileSync(join(GATE0_DIR, 'GATE0.md'), 'utf8')

describe('Gate 0 物料（人工闸不许静默漂移）', () => {
  it('探针覆盖外层 + 子帧，且没有被 @noframes 关掉子帧注入', () => {
    expect(probe).toContain('@match        https://www.kaggle.com/code/*')
    expect(probe).toContain('@match        https://*.jupyter-proxy.kaggle.net/*')
    expect(probe).not.toContain('@noframes')
    expect(probe).toContain('@grant        GM_xmlhttpRequest')
    expect(probe).toContain('@connect      127.0.0.1')
    expect(probe).toContain('@run-at       document-start')
  })

  it('三行探针各自可 grep：角色/注入、attach→ready、回环', () => {
    expect(probe).toContain('log(`role=')
    expect(probe).toContain("__gate0: 'attach'")
    expect(probe).toContain("__gate0: 'ready'")
    expect(probe).toContain("__gate0: 'status'")
    expect(probe).toContain('http://127.0.0.1:8900/api/state')
    // frame 侧必须校验 origin 与 source，否则「ready」可能来自任意窗口
    expect(probe).toContain("HOST_ORIGINS = ['https://www.kaggle.com'")
    expect(probe).toContain('e.source !== window.parent')
  })

  it('时序：iframe 轮询 + 等 .jp-Notebook（v0.1 只试一次/问太早 ⇒ 握手永远不开始）', () => {
    expect(probe).toContain('setInterval')
    expect(probe).toContain('summarizeWhenReady')
    expect(probe).toContain('60s 内没找到 iframe')
  })

  it('任何日志 URL 都不带 query（`?session=` 是会话 JWT，含邮箱/uid）', () => {
    expect(probe).toContain('`${location.origin}${location.pathname}`')
    expect(probe).not.toContain('href=${location.href}')
    expect(probe).not.toMatch(/log\(`[^`]*\$\{location\.href/) // 日志里不许直插 location.href
    expect(probe).toContain('safeHref()')
  })

  it('清单把五条判据都做成可勾选项，并留了记录位', () => {
    for (const key of ['注入', '角色', '握手', '回环', '重挂']) expect(checklist).toContain(key)
    expect(checklist.match(/^- \[ \]/gm)?.length ?? 0).toBeGreaterThanOrEqual(10)
    expect(checklist).toContain('| ① 注入 + 角色唯一 |')
    expect(checklist).toContain('失败处置')
    // 2026-10-30 实测教训：日志里的 session JWT 不许被复制传播
    expect(checklist).toContain('?session=')
    expect(checklist).toContain('先删掉 query')
  })
})
