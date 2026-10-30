/** meta.ts — userscript 元数据块的提取/解析（构建脚本与测试共用）。 */

const START = '// ==UserScript=='
const END = '// ==/UserScript=='

/** 取 `// ==UserScript==` 到 `// ==/UserScript==` 的整块（含首尾行）；缺失即抛。 */
export function extractMeta(source: string): string {
  const start = source.indexOf(START)
  if (start < 0) throw new Error('metadata block not found')
  const end = source.indexOf(END, start)
  if (end < 0) throw new Error('metadata block end not found')
  return `${source.slice(start, end + END.length)}\n`
}

export interface MetaInfo {
  name: string
  matches: string[]
  grants: string[]
  connects: string[]
}

/** 解析 `// @key value` 行；只收 name/match/grant/connect（其余忽略）。 */
export function parseMeta(block: string): MetaInfo {
  const info: MetaInfo = { name: '', matches: [], grants: [], connects: [] }
  for (const line of block.split(/\r?\n/)) {
    const m = /^\/\/\s+@([A-Za-z-]+)\s+(.+?)\s*$/.exec(line)
    if (!m) continue
    const key = m[1]
    const value = m[2]
    if (key === 'name') info.name = value ?? ''
    else if (key === 'match' && value) info.matches.push(value)
    else if (key === 'grant' && value) info.grants.push(value)
    else if (key === 'connect' && value) info.connects.push(value)
  }
  return info
}
