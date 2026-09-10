/** gen-ladder.ts — 阶梯定义生成器（§4.2/§10.2 T1.1）。
 *
 * `ladder.json` 入库副本随代码（W3）；`tools/training/data/evalboard/ladder.json`
 * 是运行时工作副本（gitignore）。s1l3b0 的去基地 stage 在此冻结存 JSON
 * （不能只写"移除基地"四个字，§4.2）。
 *
 * 用法：`bun tools/training/evalboard/gen-ladder.ts [--write-data]`
 * 缺省只写入库副本；--write-data 同步工作副本。
 */

import { writeFileSync } from 'fs'
import path from 'path'
import { buildLadder, LADDER_VERSION } from './ladder'

const HERE = path.dirname(new URL(import.meta.url).pathname)

export function renderLadderJson(): string {
  const rungs = buildLadder()
  return (
    JSON.stringify(
      {
        version: LADDER_VERSION,
        rungs,
        note: 'c4l1 基准；维度 敌数→命数→地形→基地→关卡泛化（§4.1）。god 未填 = 基线未跑（P1 T1.3）。',
      },
      null,
      2,
    ) + '\n'
  )
}

if (import.meta.main) {
  const text = renderLadderJson()
  const tracked = path.join(HERE, 'ladder.json')
  writeFileSync(tracked, text)
  console.log(`[ladder] wrote ${tracked}`)
  if (process.argv.includes('--write-data')) {
    const data = path.join(HERE, '..', 'data', 'evalboard', 'ladder.json')
    try {
      writeFileSync(data, text)
      console.log(`[ladder] wrote ${data}`)
    } catch (e) {
      console.error(`[ladder] data copy skipped: ${e instanceof Error ? e.message : String(e)}`)
    }
  }
}
