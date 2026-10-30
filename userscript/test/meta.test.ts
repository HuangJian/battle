import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { extractMeta, parseMeta } from '../scripts/meta'

const SOURCE = readFileSync(
  join(import.meta.dir, '..', 'src', 'notebook-companion', 'index.user.ts'),
  'utf8',
)
const BLOCK = extractMeta(SOURCE)
const META = parseMeta(BLOCK)

describe('userscript metadata', () => {
  it('块在文件第一行（构建产物前置的就是这一块）', () => {
    expect(SOURCE.startsWith('// ==UserScript==')).toBe(true)
    expect(BLOCK.startsWith('// ==UserScript==')).toBe(true)
    expect(BLOCK.trimEnd().endsWith('// ==/UserScript==')).toBe(true)
  })

  it('只匹配 Kaggle 外层 + 笔记本帧（v1 不给 Colab/AI Studio 加 @match）', () => {
    expect(META.matches).toEqual([
      'https://www.kaggle.com/code/*',
      'https://*.jupyter-proxy.kaggle.net/*',
    ])
    expect(META.matches.some((m) => /colab|aistudio|google/i.test(m))).toBe(false)
  })

  it('GM 权限覆盖回环请求、存储、菜单与剪贴板', () => {
    for (const g of [
      'GM_xmlhttpRequest',
      'GM_getValue',
      'GM_setValue',
      'GM_deleteValue',
      'GM_registerMenuCommand',
      'GM_setClipboard',
    ]) {
      expect(META.grants).toContain(g)
    }
    expect(META.connects).toContain('127.0.0.1')
    expect(META.connects).toContain('localhost')
  })
})
