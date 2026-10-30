/**
 * happydom.ts — 测试专用 DOM 载入器（不注册全局）。
 *
 * 每个用例 new 一个 Window：① 文件级隔离，上下半场的 querySelector 不互相看见；
 * ② 不往 Bun 全局塞 `document`，纯函数用例照常无 DOM 运行。夹具都是静态 HTML，
 * `document.write` 在空文档上解析即可。
 */
import { Window } from 'happy-dom'

export type DomHandle = Window

export function loadDom(html: string, url = 'https://example.test/'): Window {
  const window = new Window({ url })
  window.document.write(html)
  window.document.close()
  return window
}
