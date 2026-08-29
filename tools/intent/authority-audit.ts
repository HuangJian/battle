#!/usr/bin/env bun
/**
 * authority-audit.ts — 决策权审计（Goal-Space 重建手册 T1）。
 *
 * 回答「这个 NN 到底控制什么」三件事：
 *   ① 每个意图实际可达的**候选集**（经 LABEL_TO_CANDIDATE 翻译，剔除出厂默认关门候选）
 *   ② 候选集之间的**包含/等价关系**（退化检测）
 *   ③ 每 tick 的**决策比特数**
 *
 * 判据（手册 P1）：若 NN 可达集 ⊆ 规则可达集，"超越"只剩调度优势 —— 本工具就是那把尺子。
 *
 * 出厂默认关门的候选（不进有效集）：
 *   candidateMode=0 → unifiedCandidates       firingLaneMode=0 → firingLane
 *   carvePathMode=0 → carvePath                midLaneHold=0   → midLaneHold
 *   ACTION_WEIGHTS.survive = 0                 → survive
 *
 * 用法：
 *   bun tools/intent/authority-audit.ts                       # 人读表 + 回归断言
 *   bun tools/intent/authority-audit.ts --json reports/x.json # 另出机器可读
 *
 * 退出码：0 = 通过（退化结论仍成立）；1 = 词表已漂移，断言失败。
 */
import { WHITELISTS, LABEL_TO_CANDIDATE, INTENT_IDS } from '../../src/ai/intent/vocab'
import { ACTION_WEIGHTS } from '../../src/ai/god/DecisionCore'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { writeFileSync } from 'node:fs'

const params = DEFAULT_GOD_AI_PARAMS as unknown as Record<string, number>

/** 出厂默认下不可达的候选 id。 */
function unreachableCandidates(): Set<string> {
  const off = new Set<string>()
  if (ACTION_WEIGHTS.survive === 0) off.add('survive')
  if (!params.candidateMode) off.add('unifiedCandidates')
  if (!params.firingLaneMode) off.add('firingLane')
  if (!params.carvePathMode) off.add('carvePath')
  if (!params.midLaneHold) off.add('midLaneHold')
  return off
}

/** 意图 → 实际候选集（按 ACTION_WEIGHTS 降序 = 链序）。 */
function effectiveSets(off: Set<string>): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const id of INTENT_IDS) {
    const ids = new Set<string>()
    for (const row of WHITELISTS[id]) {
      const mapped = LABEL_TO_CANDIDATE[row.branch]
      if (!mapped) continue
      for (const c of mapped) if (!off.has(c)) ids.add(c)
    }
    out[id] = [...ids].sort((a, b) => ACTION_WEIGHTS[b as never] - ACTION_WEIGHTS[a as never])
  }
  return out
}

interface Relation {
  a: string
  b: string
  identical: boolean
}

function subsetRelations(sets: Record<string, string[]>): Relation[] {
  const rel: Relation[] = []
  for (const a of INTENT_IDS) {
    for (const b of INTENT_IDS) {
      if (a === b) continue
      const A = sets[a]
      if (A.length > 0 && A.every((x) => sets[b].includes(x))) {
        rel.push({ a, b, identical: A.length === sets[b].length })
      }
    }
  }
  return rel
}

const off = unreachableCandidates()
const sets = effectiveSets(off)
const rels = subsetRelations(sets)

console.log('--- 决策权审计（出厂默认参数）---')
console.log('不可达候选: ' + [...off].sort().join(', '))
console.log('')
for (const id of INTENT_IDS) {
  console.log(`${id.padEnd(15)} (${String(sets[id].length).padStart(2)})  ${sets[id].join(' > ')}`)
}
console.log('')
console.log('--- 包含关系 A ⊆ B ---')
for (const r of rels) {
  console.log(`  ${r.a} ⊆ ${r.b}${r.identical ? '   << 完全等价 >>' : ''}`)
}

// 决策比特数。两个口径都要报：
//   名义 = 执行器未掩码的类别数（ESCAPE 被 MASKED_INTENTS 剔除）
//   有效 = 候选集去重后的**行为上真正不同**的选项数 —— 这才是决策权的真实上限
const REPLAN = 30
const MASKED = new Set(['ESCAPE']) // intent-executor.ts MASKED_INTENTS
const nominal = INTENT_IDS.filter((id) => !MASKED.has(id) && sets[id].length > 0).length
const distinct = new Map<string, string[]>()
for (const id of INTENT_IDS) {
  if (MASKED.has(id)) continue
  const key = sets[id].join('|')
  if (!distinct.has(key)) distinct.set(key, [])
  distinct.get(key)!.push(id)
}
const effective = distinct.size

console.log('')
console.log(
  `名义决策比特：log2(${nominal}) = ${Math.log2(nominal).toFixed(2)} bit / ${REPLAN} tick`,
)
console.log(`               = ${(Math.log2(nominal) / REPLAN).toFixed(4)} bit/tick`)
console.log(`有效决策比特：候选集去重后仅 ${effective} 种不同行为`)
console.log(
  `               log2(${effective}) = ${Math.log2(effective).toFixed(2)} bit / ${REPLAN} tick`,
)
console.log(
  `               = ${(Math.log2(effective) / REPLAN).toFixed(4)} bit/tick   << 真实决策权`,
)
for (const ids of distinct.values()) {
  console.log(`   等价组: ${ids.join(' = ')}（候选数 ${sets[ids[0]].length}）`)
}
// 近退化集合（候选数 < MIN_MEANINGFUL）虽形式不同，但行为能力过窄，实战中近似不可用
const MIN_MEANINGFUL = 6
const meaningful = [...distinct.values()].filter((ids) => sets[ids[0]].length >= MIN_MEANINGFUL)
console.log(
  `其中候选数 ≥${MIN_MEANINGFUL} 的「有意义」行为 = ${meaningful.length} 种` +
    ` ⇒ log2(${meaningful.length}) = ${Math.log2(meaningful.length).toFixed(2)} bit / ${REPLAN} tick` +
    ` = ${(Math.log2(meaningful.length) / REPLAN).toFixed(4)} bit/tick`,
)

// ---- 回归断言：词表退化结论必须仍成立（漂移即报警）----
const identical = rels.filter((r) => r.identical).map((r) => `${r.a}=${r.b}`)
const pickupSubsetOf = rels.filter((r) => r.a === 'PICKUP' && !r.identical).map((r) => r.b)

const failures: string[] = []
if (!identical.includes('HUNT=CRUISE')) failures.push('HUNT 与 CRUISE 不再等价（词表已漂移）')
if (!identical.includes('CRUISE=HUNT')) failures.push('CRUISE 与 HUNT 不再等价（词表已漂移）')
for (const need of ['INTERCEPT', 'RETURN_DEFENSE', 'HUNT', 'HOLD_LANE', 'CRUISE']) {
  if (!pickupSubsetOf.includes(need)) failures.push(`PICKUP 不再 ⊆ ${need}`)
}

console.log('')
console.log('--- 回归断言 ---')
if (failures.length === 0) {
  console.log('PASS：HUNT ≡ CRUISE；PICKUP ⊂ 其余全部（除 CLEAR）。退化结论仍成立。')
} else {
  console.log('FAIL：')
  for (const f of failures) console.log('  - ' + f)
}

const jsonArg = process.argv.indexOf('--json')
if (jsonArg > 0 && process.argv[jsonArg + 1]) {
  const payload = {
    unreachable: [...off].sort(),
    sets,
    relations: rels,
    decisionBits: {
      nominal: { classes: nominal, bits: Math.log2(nominal), perTick: Math.log2(nominal) / REPLAN },
      effective: {
        distinctBehaviors: effective,
        bits: Math.log2(effective),
        perTick: Math.log2(effective) / REPLAN,
      },
      meaningful: {
        threshold: MIN_MEANINGFUL,
        count: meaningful.length,
        bits: Math.log2(meaningful.length),
        perTick: Math.log2(meaningful.length) / REPLAN,
      },
      replan: REPLAN,
      equivalenceGroups: [...distinct.values()],
    },
    assertions: { pass: failures.length === 0, failures },
  }
  writeFileSync(process.argv[jsonArg + 1], JSON.stringify(payload, null, 2) + '\n')
  console.log('\nwrote JSON -> ' + process.argv[jsonArg + 1])
}

process.exit(failures.length === 0 ? 0 : 1)
