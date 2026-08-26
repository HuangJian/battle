/**
 * diag-whitelist-coverage.ts — M7① 诊断：白名单分支名 → LABEL_TO_CANDIDATE → 候选 ActionId
 * 实际匹配覆盖率（修复后应 100%）。
 */
import { WHITELISTS, INTENT_IDS, LABEL_TO_CANDIDATE } from '../../src/ai/intent/vocab'
import { CANDIDATES } from '../../src/ai/god/think'

const ids = new Set<string>(CANDIDATES.map((c) => c.id))

let totalBranches = 0
let matched = 0
console.log('候选 id 全集:', [...ids].sort().join(', '))
console.log('')
for (const intent of INTENT_IDS) {
  const rows = WHITELISTS[intent]
  const mapped = new Set<string>()
  const unmapped: string[] = []
  for (const r of rows) {
    const m = LABEL_TO_CANDIDATE[r.branch]
    if (m) for (const c of m) mapped.add(c)
    else unmapped.push(r.branch)
  }
  const hit = [...mapped].filter((c) => ids.has(c))
  totalBranches += rows.length
  matched += rows.length - unmapped.length
  console.log(
    `${intent.padEnd(15)} 分支[${rows.length}] 映射候选[${hit.length}] ${JSON.stringify([...hit].sort())}${unmapped.length ? ` 未映射 ${JSON.stringify(unmapped)}` : ''}`,
  )
}
console.log('')
console.log(
  `总分支 ${totalBranches} / 已映射 ${matched} / 映射率 ${((matched / totalBranches) * 100).toFixed(0)}%`,
)
