/**
 * freeze-audit.ts — 冰冻道具经济 + 冻结窗口击杀审计（hard 难度调参用）。
 *
 * 回答两个问题：
 *   ① 冰冻道具是不是都拣到了？——每个 freeze 掉落的 spawn/pick/timeout tick、
 *      掉落格、玩家最近距离、是否在冻结期内被拾起（叠加浪费）。
 *   ② 冻结生效期间杀了多少敌人？——按连续冻结窗口统计击杀、窗口时长、
 *      窗口开始/结束时的存活敌人数。
 *
 * 只读观测：runSimulation({ itemAudit: true }) 不改变任何一局的结果
 * （同 seed/stage/params 与不开审计的 outcome 逐字节一致）。
 *
 * Usage
 *   bun tools/diag/freeze-audit.ts                     hard 35×60
 *   bun tools/diag/freeze-audit.ts --seeds 20          hard 35×20 快速筛查
 *   bun tools/diag/freeze-audit.ts --stages 3,4,7     子集
 *   bun tools/diag/freeze-audit.ts --json tmp/x.json  机器可读全量 dump
 *   bun tools/diag/freeze-audit.ts --stage-breakdown  逐关明细表
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'
import type { ItemAuditRecord, FreezeWindowRecord } from '../sim/simulation-runner'
import { parseSeeds, parseStages, parseParamSets, arg, flag } from '../lib/cli'
import { writeFileSync } from 'node:fs'

const TICK = 1000 / 60 // ms per tick, for human-readable durations

interface StageRow {
  stageIndex: number
  stageName: string
  runs: number
  wins: number
  freezeSpawned: number
  freezePicked: number
  freezeTimedOut: number
  freezeLeft: number
  kills: number
  killsDuringFreeze: number
  freezeWindows: number
  zeroKillWindows: number
  freezeTicks: number
  ticks: number
}

function median(xs: number[]): number {
  if (xs.length === 0) return 0
  const s = [...xs].sort((a, b) => a - b)
  const mid = s.length >> 1
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

function mean(xs: number[]): number {
  if (xs.length === 0) return 0
  let sum = 0
  for (const x of xs) sum += x
  return sum / xs.length
}

function pct(n: number, d: number): string {
  if (d === 0) return '—'
  return `${((n / d) * 100).toFixed(1)}%`
}

/** 距离桶：<4 / 4-8 / 8-12 / >12 / 未知 格。 */
function distBucket(cells: number): string {
  if (cells < 0) return 'unknown'
  if (cells < 4) return '<4'
  if (cells <= 8) return '4-8'
  if (cells <= 12) return '8-12'
  return '>12'
}

function main(): void {
  const seeds = parseSeeds(arg('seeds', '60')!, 60)
  const stageIdxs = parseStages(arg('stages'))
  const difficulty = arg('difficulty', 'hard')!
  const maxTicks = Number(arg('max-ticks', '18000'))
  const jsonOut = arg('json')
  const breakdown = flag('stage-breakdown')

  const overrides = parseParamSets(DEFAULT_GOD_AI_PARAMS)
  const params = { ...DEFAULT_GOD_AI_PARAMS, ...overrides }

  // ---- Aggregates ----
  const rows: StageRow[] = []
  const allItems: Array<ItemAuditRecord & { stageIndex: number; seed: number }> = []
  const allWindows: Array<
    FreezeWindowRecord & { stageIndex: number; seed: number; tickSpan: number }
  > = []
  let runs = 0
  let wins = 0
  let kills = 0
  let killsDuringFreeze = 0
  let freezeTicks = 0
  let ticks = 0
  let clearTicks = 0

  const t0 = performance.now()
  for (const si of stageIdxs) {
    const stage = STAGES[si]
    const row: StageRow = {
      stageIndex: si,
      stageName: stage.name,
      runs: 0,
      wins: 0,
      freezeSpawned: 0,
      freezePicked: 0,
      freezeTimedOut: 0,
      freezeLeft: 0,
      kills: 0,
      killsDuringFreeze: 0,
      freezeWindows: 0,
      zeroKillWindows: 0,
      freezeTicks: 0,
      ticks: 0,
    }
    for (const seed of seeds) {
      const r = runSimulation({
        seed,
        stage,
        stageIndex: si, // 真实关卡号（eval-suite 传 0，符号可能翻转）
        difficulty,
        godAIParams: params,
        maxTicks,
        sampleInterval: maxTicks,
        collectMetrics: false,
        telemetry: false,
        itemAudit: true,
      })
      const audit = r.itemAudit!
      runs++
      row.runs++
      if (r.outcome === 'stage_clear') {
        wins++
        row.wins++
      }
      kills += audit.killsTotal
      row.kills += audit.killsTotal
      killsDuringFreeze += audit.killsDuringFreeze
      row.killsDuringFreeze += audit.killsDuringFreeze
      freezeTicks += audit.freezeActiveTicks
      row.freezeTicks += audit.freezeActiveTicks
      ticks += r.ticks
      row.ticks += r.ticks
      if (r.outcome === 'stage_clear') clearTicks += r.ticks

      for (const it of audit.items) {
        allItems.push({ ...it, stageIndex: si, seed })
        if (it.type !== 'freeze') continue
        row.freezeSpawned++
        if (it.pickedTick >= 0) row.freezePicked++
        else if (it.despawnTick >= 0) row.freezeTimedOut++
        else row.freezeLeft++
      }
      for (const w of audit.freezeWindows) {
        const end = w.endTick < 0 ? r.ticks : w.endTick
        allWindows.push({ ...w, stageIndex: si, seed, tickSpan: end - w.startTick })
        row.freezeWindows++
        if (w.kills === 0) row.zeroKillWindows++
      }
    }
    rows.push(row)
    const done = rows.length * seeds.length
    if (rows.length % 5 === 0 || rows.length === stageIdxs.length) {
      const el = ((performance.now() - t0) / 1000).toFixed(1)
      process.stderr.write(`  … ${done}/${stageIdxs.length * seeds.length} runs (${el}s)\n`)
    }
  }
  const wallS = (performance.now() - t0) / 1000

  // ---- Freeze-item census ----
  const freezeItems = allItems.filter((i) => i.type === 'freeze')
  const picked = freezeItems.filter((i) => i.pickedTick >= 0)
  const timedOut = freezeItems.filter((i) => i.pickedTick < 0 && i.despawnTick >= 0)
  const leftOnField = freezeItems.filter((i) => i.pickedTick < 0 && i.despawnTick < 0)
  const unpicked = freezeItems.filter((i) => i.pickedTick < 0)
  const latencies = picked.map((i) => i.pickedTick - i.spawnTick)
  const stacked = picked.filter((i) => i.pickedWhileFreezeActive)

  const bucket = new Map<string, number>()
  for (const i of unpicked) {
    const b = distBucket(i.minDistCells)
    bucket.set(b, (bucket.get(b) ?? 0) + 1)
  }
  const spawnBaseBucket = { near: 0, mid: 0, far: 0, unknown: 0 }
  for (const i of freezeItems) {
    const d = i.playerDistToBaseAtSpawn
    if (d < 0) spawnBaseBucket.unknown++
    else if (d <= 6) spawnBaseBucket.near++
    else if (d <= 12) spawnBaseBucket.mid++
    else spawnBaseBucket.far++
  }

  // ---- Freeze windows ----
  const winKills = allWindows.map((w) => w.kills)
  const zeroWin = allWindows.filter((w) => w.kills === 0).length
  const winSpan = allWindows.map((w) => w.tickSpan)
  const winKillsPerSec = allWindows
    .filter((w) => w.tickSpan > 0)
    .map((w) => w.kills / ((w.tickSpan * TICK) / 1000))
  const startedByItem = allWindows.filter((w) => w.startedByFreezeItem).length

  // ---- Print ----
  const L: string[] = []
  L.push('')
  L.push(
    `FREEZE / ITEM AUDIT · ${difficulty} · ${stageIdxs.length} stages × ${seeds.length} seeds = ${runs} runs · maxTicks ${maxTicks}` +
      (Object.keys(overrides).length ? ` · overrides ${JSON.stringify(overrides)}` : ''),
  )
  L.push(
    `wall ${wallS.toFixed(1)}s · win ${pct(wins, runs)} (${wins}/${runs}) · mean ticks ${mean(rows.map((r) => r.ticks / r.runs)).toFixed(0)}`,
  )
  L.push('')
  L.push('— FREEZE ITEM LIFECYCLE —')
  L.push(
    `spawned ${freezeItems.length} · picked ${picked.length} (${pct(picked.length, freezeItems.length)}) · ` +
      `timed out ${timedOut.length} (${pct(timedOut.length, freezeItems.length)}) · ` +
      `left on field at run end ${leftOnField.length}`,
  )
  L.push(
    `per run: spawned ${(freezeItems.length / runs).toFixed(3)} · picked ${(picked.length / runs).toFixed(3)}`,
  )
  L.push(
    `pickup latency (ticks): median ${median(latencies).toFixed(0)} · mean ${mean(latencies).toFixed(0)} · ` +
      `p90 ${[...latencies].sort((a, b) => a - b)[Math.max(0, Math.ceil(latencies.length * 0.9) - 1)] ?? 0}`,
  )
  L.push(
    `picked while a freeze window was already running (wasted stacking): ${stacked.length} (${pct(stacked.length, picked.length)})`,
  )
  L.push(
    `unpicked ${unpicked.length}: player min distance (cells) ` +
      `[<4 ${bucket.get('<4') ?? 0} · 4-8 ${bucket.get('4-8') ?? 0} · 8-12 ${bucket.get('8-12') ?? 0} · >12 ${bucket.get('>12') ?? 0} · n/a ${bucket.get('unknown') ?? 0}]`,
  )
  const reachable = unpicked.filter((i) => i.minDistCells >= 0 && i.minDistCells <= 8).length
  L.push(`  → came within the 8-cell urgent range but did not take it: ${reachable}`)
  L.push(
    `spawn distance to base at spawn (cells): near(≤6) ${spawnBaseBucket.near} · mid(7-12) ${spawnBaseBucket.mid} · far(>12) ${spawnBaseBucket.far}`,
  )
  L.push('')
  L.push('— FREEZE WINDOWS —')
  L.push(
    `windows ${allWindows.length} (started by freeze item ${startedByItem}) · per run ${(allWindows.length / runs).toFixed(3)} · ` +
      `freeze active ${((freezeTicks / ticks) * 100).toFixed(2)}% of sim ticks`,
  )
  L.push(
    `kills during freeze ${killsDuringFreeze} / ${kills} total (${pct(killsDuringFreeze, kills)}) · ` +
      `kills per window mean ${mean(winKills).toFixed(2)} · median ${median(winKills)}`,
  )
  L.push(
    `zero-kill windows ${zeroWin} (${pct(zeroWin, allWindows.length)}) · ` +
      `window length (ticks) median ${median(winSpan).toFixed(0)} mean ${mean(winSpan).toFixed(0)} · ` +
      `kills/sec during freeze ${mean(winKillsPerSec).toFixed(2)}`,
  )
  const winEnemies = allWindows.map((w) => w.enemiesAtStart)
  L.push(
    `enemies alive at window start: mean ${mean(winEnemies).toFixed(2)} · zero-enemy windows ${allWindows.filter((w) => w.enemiesAtStart === 0).length}`,
  )
  L.push('')

  // ---- <2-kill window diagnosis (user request 2026-10-29) ----
  const lowKill = allWindows.filter((w) => w.kills < 2)
  const highKill = allWindows.filter((w) => w.kills >= 2)
  const branchMix = (ws: typeof allWindows): string => {
    const totals = new Map<string, number>()
    let sum = 0
    for (const w of ws) {
      for (const [b, n] of Object.entries(w.branchTicks ?? {})) {
        totals.set(b, (totals.get(b) ?? 0) + n)
        sum += n
      }
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([b, n]) => `${b} ${((n / sum) * 100).toFixed(0)}%`)
      .join(' · ')
  }
  const distBucket2 = (d: number): string => {
    if (d < 0) return 'n/a'
    if (d === 0) return '0'
    if (d <= 5) return '1-5'
    if (d <= 10) return '6-10'
    return '>10'
  }
  L.push('— LOW-KILL WINDOWS (kills < 2) —')
  L.push(
    `${lowKill.length}/${allWindows.length} windows (${pct(lowKill.length, allWindows.length)}) · ` +
      `ticks ${pct(
        lowKill.reduce((s, w) => s + w.tickSpan, 0),
        allWindows.reduce((s, w) => s + w.tickSpan, 0),
      )} of freeze ticks`,
  )
  const lowZeroEnemy = lowKill.filter((w) => w.enemiesAtStart === 0).length
  const lowDead = lowKill.filter((w) => w.playerDeadTicks > 0).length
  const lowShopping = lowKill.filter((w) => w.itemsPicked > 0).length
  L.push(
    `opened with 0 enemies ${lowZeroEnemy} (${pct(lowZeroEnemy, lowKill.length)}) · player died during ${lowDead} · ` +
      `picked ≥1 item during ${lowShopping} (${pct(lowShopping, lowKill.length)})`,
  )
  L.push(
    `nearest enemy at open: ${['0', '1-5', '6-10', '>10', 'n/a']
      .map((b) => {
        const n = lowKill.filter((w) => distBucket2(w.nearestEnemyDistAtStart) === b).length
        return `${b}:${n}`
      })
      .join(' · ')}`,
  )
  L.push(
    `travel (cells) median ${median(lowKill.map((w) => w.playerTravelCells)).toFixed(1)} · ` +
      `items picked median ${median(lowKill.map((w) => w.itemsPicked))} · ` +
      `first kill offset median ${median(lowKill.filter((w) => w.firstKillOffset >= 0).map((w) => w.firstKillOffset)).toFixed(0)} ticks`,
  )
  L.push(`branch mix (<2 kill): ${branchMix(lowKill)}`)
  L.push(`branch mix (≥2 kill): ${branchMix(highKill)}`)
  const lowEnded = lowKill.filter((w) => w.endTick < 0).length
  const fullLow = lowKill.filter((w) => w.endTick >= 0 && w.tickSpan >= 600)
  const fullHigh = highKill.filter((w) => w.endTick >= 0 && w.tickSpan >= 600)
  const kindMix = (ws: typeof allWindows): string => {
    const totals = new Map<string, number>()
    let sum = 0
    for (const w of ws) {
      for (const [k, n] of Object.entries(w.hitKinds ?? {})) {
        totals.set(k, (totals.get(k) ?? 0) + n)
        sum += n
      }
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([k, n]) => `${k} ${((n / sum) * 100).toFixed(0)}%`)
      .join(' · ')
  }
  L.push(
    `${lowEnded}/${lowKill.length} low-kill windows ended with the run (freeze picked at stage end — no fight left) · ` +
      `full-length (<2 kill) ${fullLow.length} vs ≥2 kill ${fullHigh.length}`,
  )
  L.push(
    `full-length only — low-kill: travel ${median(fullLow.map((w) => w.playerTravelCells)).toFixed(0)} cells · ` +
      `items ${median(fullLow.map((w) => w.itemsPicked))} · shots ${median(fullLow.map((w) => w.playerShots))} · ` +
      `hits ${median(fullLow.map((w) => w.enemyHits))} · minEnemyDist ${median(fullLow.map((w) => w.minEnemyDist))} · ` +
      `aligned ${median(fullLow.map((w) => w.alignedTicks))} / los ${median(fullLow.map((w) => w.losTicks))} ticks`,
  )
  L.push(
    `full-length only — ≥2-kill: travel ${median(fullHigh.map((w) => w.playerTravelCells)).toFixed(0)} cells · ` +
      `items ${median(fullHigh.map((w) => w.itemsPicked))} · shots ${median(fullHigh.map((w) => w.playerShots))} · ` +
      `hits ${median(fullHigh.map((w) => w.enemyHits))} · minEnemyDist ${median(fullHigh.map((w) => w.minEnemyDist))} · ` +
      `aligned ${median(fullHigh.map((w) => w.alignedTicks))} / los ${median(fullHigh.map((w) => w.losTicks))} ticks`,
  )
  L.push(`hit kinds — low-kill: ${kindMix(fullLow)}`)
  L.push(`hit kinds — ≥2-kill: ${kindMix(fullHigh)}`)
  L.push('')

  if (breakdown) {
    L.push('— PER-STAGE —')
    L.push(
      'stage                      runs  win    freeze spawn/pick/to/left   kills  kFreeze  share  windows  zeroK  fz%',
    )
    for (const r of rows) {
      const name = `${r.stageIndex + 1}. ${r.stageName}`.padEnd(26)
      const share = r.kills > 0 ? pct(r.killsDuringFreeze, r.kills) : '—'
      const fzPct = r.ticks > 0 ? ((r.freezeTicks / r.ticks) * 100).toFixed(1) : '0.0'
      L.push(
        `${name} ${String(r.runs).padStart(4)}  ${pct(r.wins, r.runs).padStart(5)}   ` +
          `${String(r.freezeSpawned).padStart(3)}/${String(r.freezePicked).padEnd(3)}/${String(r.freezeTimedOut).padEnd(3)}/${String(r.freezeLeft).padEnd(3)}  ` +
          `${String(r.kills).padStart(5)}  ${String(r.killsDuringFreeze).padStart(7)}  ${share.padStart(5)}  ` +
          `${String(r.freezeWindows).padStart(6)}  ${String(r.zeroKillWindows).padStart(5)}  ${fzPct.padStart(5)}`,
      )
    }
  }

  // Non-freeze item census (context: is freeze uniquely leaky?)
  const byType = new Map<string, { spawn: number; pick: number; timeout: number; left: number }>()
  for (const it of allItems) {
    let rec = byType.get(it.type)
    if (!rec) {
      rec = { spawn: 0, pick: 0, timeout: 0, left: 0 }
      byType.set(it.type, rec)
    }
    rec.spawn++
    if (it.pickedTick >= 0) rec.pick++
    else if (it.despawnTick >= 0) rec.timeout++
    else rec.left++
  }
  L.push('— ALL ITEM TYPES (spawn / picked / timed out / left) —')
  for (const [type, v] of [...byType.entries()].sort((a, b) => b[1].spawn - a[1].spawn)) {
    L.push(
      `  ${type.padEnd(10)} ${String(v.spawn).padStart(5)} / ${String(v.pick).padStart(5)} (${pct(v.pick, v.spawn).padStart(6)}) / ` +
        `${String(v.timeout).padStart(4)} / ${String(v.left).padStart(4)}`,
    )
  }
  L.push('')

  console.log(L.join('\n'))

  if (jsonOut) {
    writeFileSync(
      jsonOut,
      JSON.stringify(
        {
          meta: {
            difficulty,
            stages: stageIdxs.length,
            seeds: seeds.length,
            runs,
            maxTicks,
            wallS,
            winRate: wins / runs,
            overrides,
          },
          stages: rows,
          items: allItems,
          freezeWindows: allWindows,
          summary: {
            kills,
            killsDuringFreeze,
            freezeTicks,
            ticks,
            freezeSpawned: freezeItems.length,
            freezePicked: picked.length,
            freezeTimedOut: timedOut.length,
            freezeLeft: leftOnField.length,
            clearTicks,
          },
        },
        null,
        0,
      ),
    )
    console.log(`json → ${jsonOut}`)
  }
}

main()
