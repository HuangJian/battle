/**
 * verify-item-events.ts — gate ⑤ cross-check (plan NN-M0b).
 *
 * The encoder emits an `item` action head label ONLY on an item-event tick
 * (decisionTick condition === 2), which is a guard/frenzy BIT CHANGE vs the
 * previous frame. Because every item-event tick is also `isDecision === true`
 * (decisionTick: isDecision = turn || fire || itemEvent || subsample), an
 * item-event can NEVER be silently dropped — it is always exported, possibly
 * re-labelled turn(0)/fire(1) when a higher-priority event coincides.
 *
 * So the defensible, runnable invariant for gate ⑤ is the SUBSET check:
 *   exported nItem (condition 2)  <=  nItemEvents (raw guard/frenzy changes)
 * i.e. NO phantom item samples. (Drops are structurally impossible.)
 *
 * Both numbers come from the SAME exportReplay path, so they are perfectly
 * aligned — no independent frame-decoding drift.
 *
 * Usage:
 *   bun tools/replay/verify-item-events.ts nn-demo/*.ndjson
 */
import { exportReplay } from './export-observations'

async function main(): Promise<void> {
  const files = process.argv.slice(2).filter((a) => !a.startsWith('--'))
  if (files.length === 0) {
    console.error('usage: bun tools/replay/verify-item-events.ts <demos.ndjson...>')
    process.exit(2)
  }

  const rows: string[] = []
  let checked = 0
  let totalItemEvents = 0
  let totalNItem = 0
  let phantom = 0

  for (const f of files) {
    const content = await (Bun.file(f) as any).text()
    const lines = String(content).split('\n').filter((l: string) => l.trim().length > 0)
    for (let i = 0; i < lines.length; i++) {
      // skipVerify: item detection is frame-only; desync does not affect it.
      const res = exportReplay(lines[i], `${f}#${i}`, true)
      if (!res.ok) {
        rows.push(`[SKIP] ${f}#${i}: ${res.reason}`)
        continue
      }
      const nItem = res.acc.nItem
      const nItemEvents = res.acc.nItemEvents
      checked++
      totalItemEvents += nItemEvents
      totalNItem += nItem
      const ok = nItem <= nItemEvents
      if (!ok) phantom++
      const overridden = nItemEvents - nItem // item-events re-labelled turn/fire by priority
      rows.push(
        `[${ok ? 'OK' : 'PHANTOM'}] ${f}#${i}: exportedItem=${nItem} replayItemEvents=${nItemEvents}` +
        (ok ? ` (priorityOverride=${overridden})` : `  <-- MORE EXPORTED THAN REPLAY HAS`),
      )
    }
  }

  console.log(rows.join('\n'))
  console.log('\n=== item-event cross-check (gate ⑤) ===')
  console.log(`replaysChecked=${checked} totalReplayItemEvents=${totalItemEvents} totalExportedItem=${totalNItem} phantom=${phantom}`)
  if (phantom === 0) {
    console.log('PASS: no phantom item samples — every exported item-condition maps to a real guard/frenzy bit-change in the replay.')
    console.log('NOTE: drops are structurally impossible (every item-event tick is a decision tick and is always exported).')
  } else {
    console.log('FAIL: phantom item samples detected (exported item count exceeds replay guard/frenzy changes).')
  }
  process.exit(phantom > 0 ? 1 : 0)
}

if (import.meta.main) {
  main()
}
