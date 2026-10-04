/**
 * server-theme-hot-css.test.ts — theme.css 热加载：改样式不必重启控制台（2026-10-04）
 *
 * 分层：src/server/theme-css.ts（mtime/size 缓存现读）· src/web/render.tsx（`css` 注入出口）
 *
 * 病根：theme.css 经 Bun text import 在**模块加载时**读一次（进程内模块图缓存），
 * SSR 进程不重启就不重读。修法：服务端每请求 statSync（微秒级），变了才重读、
 * 没变零读盘；CSS 从 render 的 `css` 参数进 `<style>`（缺省仍是构建期文本导入）。
 */
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, statSync, utimesSync, writeFileSync } from 'fs'
import os from 'os'
import { join } from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { livePageCss, makeCssHotReader, THEME_CSS_PATH } from '../src/server/theme-css'
import type { LogPayload } from '../src/web/view'
import { renderLogPage } from '../src/web/render'

/** 固定 mtime 的临时 css（不碰仓库真文件；显式 utimes ⇒ 与墙钟/FS 精度无关）。 */
function scratchCss(content: string, mtimeSec = 1_600_000_000): { path: string; dir: string } {
  const dir = mkdtempSync(join(os.tmpdir(), 'theme-hot-'))
  const path = join(dir, 'theme.css')
  writeFileSync(path, content)
  utimesSync(path, mtimeSec, mtimeSec)
  return { path, dir }
}

describe('theme.css 热加载（读器）', () => {
  it('首读返回内容；mtime 前进 ⇒ 下次读到新内容（改样式不重启即生效）', () => {
    const { path, dir } = scratchCss('.a{}')
    try {
      const read = makeCssHotReader(path)
      expect(read()).toBe('.a{}')
      writeFileSync(path, '.a{color:red}')
      utimesSync(path, 1_600_000_001, 1_600_000_001)
      expect(read()).toBe('.a{color:red}')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('文件没变 ⇒ 不重读（读盘次数不增；stat 每拍照问——那是判据）', () => {
    const { path, dir } = scratchCss('.a{}')
    try {
      let reads = 0
      let stats = 0
      const read = makeCssHotReader(path, {
        readFile: (p) => {
          reads++
          return readFileSync(p, 'utf8')
        },
        stat: (p) => {
          stats++
          const s = statSync(p)
          return { mtimeMs: s.mtimeMs, size: s.size }
        },
      })
      expect(read()).toBe('.a{}')
      expect(read()).toBe('.a{}')
      expect(reads).toBe(1)
      expect(stats).toBe(2)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('★ size 是第二判据：mtime 不动（粗粒度 FS / 同毫秒连写）但长度变了 ⇒ 仍重读', () => {
    const { path, dir } = scratchCss('.a{}')
    try {
      const read = makeCssHotReader(path)
      expect(read()).toBe('.a{}')
      writeFileSync(path, '.a{color:red}') // 更长
      utimesSync(path, 1_600_000_000, 1_600_000_000) // 钉回同一个 mtime
      expect(read()).toBe('.a{color:red}')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('读不了：上次好值优先；从未读到 ⇒ 兜底（不抛、不发空样式）', () => {
    const { path, dir } = scratchCss('.a{}')
    try {
      const never = makeCssHotReader(join(dir, 'missing.css'), { fallback: () => '.fallback{}' })
      expect(never()).toBe('.fallback{}')
      const read = makeCssHotReader(path, { fallback: () => '.fallback{}' })
      expect(read()).toBe('.a{}')
      rmSync(path)
      expect(read()).toBe('.a{}')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('生产单例现读的就是仓库 theme.css（不是另一份副本）', () => {
    expect(livePageCss()).toBe(readFileSync(THEME_CSS_PATH, 'utf8'))
  })
})

describe('SSR 出口（注入的 css 进 <style>；缺省 = 构建期 theme.css）', () => {
  const payload: LogPayload = {
    component: 'hubServer',
    label: 'HUB',
    log: null,
    exists: false,
    fileSize: 0,
    lines: [],
    truncated: false,
  }
  const opts = { components: [], follow: false, lines: 20 }

  it('renderLogPage：注入的 css **替换**构建期内联（不是拼在其后）', () => {
    const html = renderLogPage(payload, opts, { css: '.tc-hot{color:red}' })
    expect(html).toContain('<style>.tc-hot{color:red}</style>')
    expect(html).not.toContain('--fs-xs') // 真 theme.css 的阶梯 token：注入后不该在场
  })

  it('不注入 ⇒ 仍是构建期 theme.css（测试与旧调用方行为不变）', () => {
    expect(renderLogPage(payload, opts)).toContain('--fs-xs')
  })

  it('接线：server.ts 全部页面渲染口都注入现读 CSS（新增页面忘注入 ⇒ 红）', () => {
    const src = readFileSync(join(DASHBOARD_ROOT, 'src', 'server', 'server.ts'), 'utf8')
    // 只看代码行：注释里的名字（如文件头 `render.tsx renderConsolePage`）与 import 行不算调用点。
    const code = src
      .split('\n')
      .filter(
        (l) =>
          !l.trim().startsWith('//') &&
          !l.trim().startsWith('*') &&
          !l.trim().startsWith('/*') &&
          !l.trim().startsWith('import'),
      )
      .join('\n')
    const calls = [...code.matchAll(/render(Console|Eval|Log)Page\(/g)]
    expect(calls.length).toBe(3)
    for (const m of calls) {
      const win = code.slice(m.index ?? 0, (m.index ?? 0) + 600)
      expect(win, `${m[0]} 缺 livePageCss() 注入`).toContain('livePageCss()')
    }
  })
})
