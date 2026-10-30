import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { extractMeta } from './meta'

/**
 * build-userscript.ts — 元数据块 + debug/prod 两遍构建。
 *
 * 元数据只留一份：入口文件顶部是唯一来源，打包后把它剥掉再前置（minify 会剥注释，
 * debug 不一定，所以两条腿都走同一套「剥了再拼」）。产物：
 *   dist/notebook-companion.user.js         （压缩，交付）
 *   dist/notebook-companion.debug.user.js   （未压缩 + inline sourcemap，排查）
 */
const ROOT = join(import.meta.dir, '..')
const ENTRY = join(ROOT, 'src', 'notebook-companion', 'index.user.ts')
const DIST = join(ROOT, 'dist')
const META_RE = /\/\/ ==UserScript==[\s\S]*?\/\/ ==\/UserScript==\r?\n?/

async function build(minify: boolean, name: string): Promise<void> {
  const result = await Bun.build({
    entrypoints: [ENTRY],
    target: 'browser',
    format: 'iife',
    minify,
    sourcemap: minify ? 'none' : 'inline',
  })
  if (!result.success) {
    for (const log of result.logs) console.error(log.message)
    throw new Error('userscript bundle failed')
  }
  const output = result.outputs[0]
  if (!output) throw new Error('userscript bundle produced no output')
  const meta = extractMeta(readFileSync(ENTRY, 'utf8'))
  const body = (await output.text()).replace(META_RE, '')
  writeFileSync(join(DIST, name), `${meta}\n${body}`, 'utf8')
}

if (import.meta.main) {
  mkdirSync(DIST, { recursive: true })
  await build(false, 'notebook-companion.debug.user.js')
  await build(true, 'notebook-companion.user.js')
  console.log('[userscript] wrote dist/notebook-companion.user.js + .debug.user.js')
}
