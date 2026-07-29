/**
 * analyze-profile.ts — aggregate a V8 .cpuprofile into self-time hot spots.
 *
 * Self-time = time the function's OWN body was on top of the stack (the
 * function actually executing when the sampler fired). This is the right
 * lens for finding bottlenecks: it points at the code we can speed up, not
 * its callers.
 *
 * Usage: bun tools/perf/analyze-profile.ts <file.cpuprofile> [topN]
 */
import { readFileSync } from 'node:fs'

const file = process.argv[2]
const topN = Number(process.argv[3] ?? 30)
if (!file) {
  console.error('usage: analyze-profile.ts <file.cpuprofile> [topN]')
  process.exit(1)
}
const prof = JSON.parse(readFileSync(file, 'utf8'))

const nodes: any[] = prof.nodes
const samples: number[] = prof.samples
const timeDeltas: number[] = prof.timeDeltas

const byId = new Map<number, any>()
for (const n of nodes) byId.set(n.id, n)

// Bun stores timeDeltas in microseconds.
const selfMs = new Map<number, number>()
let totalMs = 0
for (let i = 0; i < samples.length; i++) {
  const ms = timeDeltas[i] / 1000
  totalMs += ms
  const id = samples[i]
  selfMs.set(id, (selfMs.get(id) ?? 0) + ms)
}

function shortUrl(u: string): string {
  const m = u.match(/src\/(.+)$/) || u.match(/tools\/(.+)$/) || u.match(/battle\/(.+)$/)
  return m ? m[1] : u
}

// Aggregate by `function @ module` (line stripped). Stripping the line keeps
// a single function together even if the sampler reports it at multiple
// inlined positions — giving an unambiguous hot list.
const agg = new Map<string, { ms: number; file: string; fn: string }>()
for (const [id, ms] of selfMs) {
  const n = byId.get(id)
  if (!n) continue
  const cf = n.callFrame
  const fn = cf.functionName || '(anonymous)'
  const su = shortUrl(cf.url || '')
  const key = `${fn} @ ${su}`
  const cur = agg.get(key) ?? { ms: 0, file: su, fn }
  cur.ms += ms
  agg.set(key, cur)
}

const sorted = [...agg.values()].sort((a, b) => b.ms - a.ms)

console.log(`total sampled: ${totalMs.toFixed(0)}ms  samples: ${samples.length}\n`)
console.log(`=== TOP ${topN} BY SELF-TIME (function @ module) ===`)
let cum = 0
for (const r of sorted.slice(0, topN)) {
  const pct = (r.ms / totalMs) * 100
  cum += pct
  console.log(
    `${r.ms.toFixed(1).padStart(9)}ms  ${pct.toFixed(1).padStart(5)}%  ${cum
      .toFixed(1)
      .padStart(5)}%  ${r.fn}  @ ${r.file}`,
  )
}

// Module-level aggregate.
const modMs = new Map<string, number>()
for (const r of sorted) modMs.set(r.file, (modMs.get(r.file) ?? 0) + r.ms)
console.log(`\n=== BY MODULE (self-time) ===`)
for (const [f, ms] of [...modMs.entries()].sort((a, b) => b[1] - a[1])) {
  console.log(
    `${ms.toFixed(1).padStart(9)}ms  ${((ms / totalMs) * 100).toFixed(1).padStart(5)}%  ${f}`,
  )
}
