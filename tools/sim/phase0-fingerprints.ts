#!/usr/bin/env bun
/**
 * phase0-fingerprints.ts — Phase 0 逐敌种画像报告（plan/x3-power-followup.plan.md T3）。
 *
 * 输入 = `tools/sim/eval-course-ckpt.ts` 产出的 JSONL（每局一行，T3 起携带
 * `hitsByKind`/`killsByKind`/`exposureByKind`/`firstHitKind`/`firstKillKind`/`killOrder`/
 * `killerKinds`）。输出 = 四指纹 + 预注册分支判决（**唯一**）+ 样本声明，纯离线，
 * 零仿真成本（重跑同一批 JSONL 逐字节同一份报告）。
 *
 * 四指纹（口径冻结，见计划 §T3「预注册分支」；执行时禁止改口径）：
 *   ① killer-kind      —— 谁杀了我（主判据；败局 = 玩家死亡）
 *   ② 首命中/首杀 kind —— 先打谁
 *   ③ 击杀顺序         —— power 是否恒死最后 / 从没被杀
 *   ④ 曝光归一命中份额 —— 分母 = 各敌种「存活 × 接战时长」积分（`exposureByKind`）；
 *                          **禁用「在场份额」作分母**（那是循环论证）
 *
 * 用法：
 *   bun tools/sim/phase0-fingerprints.ts \
 *     --in baseline=tmp/x3-phase0/baseline-it333.jsonl \
 *     --in verdict=tmp/x3-phase0/verdict-it30.jsonl [--out tmp/x3-phase0/report.md]
 */
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'

/** 索引契约 = tools/sim/export-eval-game.ts::ENEMY_KIND_ORDER。 */
const KINDS = ['basic', 'fast', 'power', 'armor'] as const
type Kind = (typeof KINDS)[number]

interface Row {
  label: string
  stageId: number
  stageName?: string
  seed: number
  win: boolean
  cleared: boolean
  outcome: string
  ticks: number
  kills: number
  enemyHits: number
  playerHits: number
  playerDamageTaken: number
  hitsByKind: number[]
  killsByKind: number[]
  exposureByKind: number[]
  firstHitKind: string | null
  firstKillKind: string | null
  killOrder: string[]
  killerKinds: (string | null)[]
}

/** Wilson 95% 区间（比例）。 */
function wilson(k: number, n: number): [number, number] {
  if (n === 0) return [0, 0]
  const z = 1.96
  const p = k / n
  const d = 1 + (z * z) / n
  const c = p + (z * z) / (2 * n)
  const s = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))
  return [Math.max(0, (c - s) / d), Math.min(1, (c + s) / d)]
}

const pct = (x: number): string => `${(100 * x).toFixed(1)}%`
const pctCi = (k: number, n: number): string => {
  const [lo, hi] = wilson(k, n)
  return `${pct(k / Math.max(1, n))} [${pct(lo)}, ${pct(hi)}]`
}

interface Agg {
  label: string
  games: number
  wins: number
  /** 死亡局 = 有 killerKinds 的局（1 命课 = 玩家死亡）。 */
  deathGames: number
  deaths: number
  killsTotal: number
  hitsTotal: number
  /** ① killer-kind（按死亡次数计）。 */
  killerByKind: Record<Kind, number>
  killerUnknown: number
  /** ② 首命中 / 首杀（按局计）。 */
  firstHitByKind: Record<Kind | 'none', number>
  firstKillByKind: Record<Kind | 'none', number>
  /** ③ 击杀顺序。 */
  powerPresentGames: number
  powerNeverKilled: number
  powerKilledLast: number
  powerKillPosSum: number
  powerKillPosN: number
  /** ④ 曝光/命中/击杀三向量（池内求和）。 */
  hits: number[]
  kills: number[]
  exposure: number[]
  /** 胜/败条件化（③ 不条件化会被「败局当然没打完」混淆；这里分开统计）。 */
  won: CondAgg
  lost: CondAgg
}

/** 胜/败子集的 ①/③④ 统计（口径与主池一致，只是分母不同）。 */
interface CondAgg {
  games: number
  deaths: number
  killerByKind: Record<Kind, number>
  powerPresent: number
  powerHit: number
  powerKilled: number
  firstHitPower: number
  hits: number[]
  kills: number[]
  exposure: number[]
}

function newCond(): CondAgg {
  return {
    games: 0,
    deaths: 0,
    killerByKind: zeroKinds(),
    powerPresent: 0,
    powerHit: 0,
    powerKilled: 0,
    firstHitPower: 0,
    hits: [0, 0, 0, 0],
    kills: [0, 0, 0, 0],
    exposure: [0, 0, 0, 0],
  }
}

function zeroKinds(): Record<Kind, number> {
  return { basic: 0, fast: 0, power: 0, armor: 0 }
}

function newAgg(label: string): Agg {
  return {
    label,
    games: 0,
    wins: 0,
    deathGames: 0,
    deaths: 0,
    killsTotal: 0,
    hitsTotal: 0,
    killerByKind: zeroKinds(),
    killerUnknown: 0,
    firstHitByKind: { ...zeroKinds(), none: 0 },
    firstKillByKind: { ...zeroKinds(), none: 0 },
    powerPresentGames: 0,
    powerNeverKilled: 0,
    powerKilledLast: 0,
    powerKillPosSum: 0,
    powerKillPosN: 0,
    hits: [0, 0, 0, 0],
    kills: [0, 0, 0, 0],
    exposure: [0, 0, 0, 0],
    won: newCond(),
    lost: newCond(),
  }
}

function accumulate(a: Agg, r: Row): void {
  a.games++
  if (r.win || r.cleared) a.wins++
  a.killsTotal += r.kills
  a.hitsTotal += r.enemyHits
  for (let i = 0; i < 4; i++) {
    a.hits[i] += r.hitsByKind?.[i] ?? 0
    a.kills[i] += r.killsByKind?.[i] ?? 0
    a.exposure[i] += r.exposureByKind?.[i] ?? 0
  }
  // ①
  if (r.killerKinds.length > 0) {
    a.deathGames++
    for (const kk of r.killerKinds) {
      a.deaths++
      if (kk && (KINDS as readonly string[]).includes(kk)) a.killerByKind[kk as Kind]++
      else a.killerUnknown++
    }
  }
  // ②
  const fh = r.firstHitKind
  if (r.enemyHits > 0 && fh && (KINDS as readonly string[]).includes(fh)) {
    a.firstHitByKind[fh as Kind]++
  } else if (r.enemyHits === 0) a.firstHitByKind.none++
  const fk = r.firstKillKind
  if (r.killOrder.length > 0 && fk && (KINDS as readonly string[]).includes(fk)) {
    a.firstKillByKind[fk as Kind]++
  } else if (r.killOrder.length === 0) a.firstKillByKind.none++
  // ③
  const powerIdx = KINDS.indexOf('power')
  if ((r.exposureByKind?.[powerIdx] ?? 0) > 0) {
    a.powerPresentGames++
    const pos = r.killOrder.indexOf('power')
    if (pos < 0) a.powerNeverKilled++
    else {
      a.powerKillPosN++
      a.powerKillPosSum += pos + 1
      if (pos === r.killOrder.length - 1) a.powerKilledLast++
    }
  }
  // 胜/败条件化
  const c = r.win || r.cleared ? a.won : a.lost
  c.games++
  c.deaths += r.killerKinds.length
  for (const kk of r.killerKinds) {
    if (kk && (KINDS as readonly string[]).includes(kk)) c.killerByKind[kk as Kind]++
  }
  for (let i = 0; i < 4; i++) {
    c.hits[i] += r.hitsByKind?.[i] ?? 0
    c.kills[i] += r.killsByKind?.[i] ?? 0
    c.exposure[i] += r.exposureByKind?.[i] ?? 0
  }
  if ((r.exposureByKind?.[powerIdx] ?? 0) > 0) {
    c.powerPresent++
    if ((r.hitsByKind?.[powerIdx] ?? 0) > 0) c.powerHit++
    if ((r.killsByKind?.[powerIdx] ?? 0) > 0) c.powerKilled++
    if (r.firstHitKind === 'power') c.firstHitPower++
  }
}

/** ④ 曝光归一命中份额（各 kind 的每千敌车 tick 命中率 → 归一化份额）。 */
function hitRates(a: Agg): { rate: number[]; share: number[]; conv: number[] } {
  const rate = a.hits.map((h, i) => (a.exposure[i] > 0 ? h / a.exposure[i] : 0))
  const sum = rate.reduce((x, y) => x + y, 0)
  const share = rate.map((x) => (sum > 0 ? x / sum : 0))
  const conv = a.hits.map((h, i) => (h > 0 ? a.kills[i] / h : 0))
  return { rate, share, conv }
}

/**
 * 预注册分支判决（口径冻结于计划 §T3；**执行时不得改口径**）。
 *
 * 三条分支按指纹定义成「成立/不成立」，再按计划写明的 tie-break 定归属：
 *   F1（①处刑）  = killer=power 份额 A ≥ 0.50
 *   F2（②绕开）  = 首命中非 power B ≥ 0.60 **且** power 存活最久 C ≥ 0.60
 *   F3（③打不死）= power 命中→击杀转化 E ≤ 0.40 且 归一命中份额 S ≥ 0.20
 * 唯一成立 → 该分支；**F1 与 F2 同时成立 → tie-break：看 ② 首枪（首命中）分布**
 * （power 占首命中 ≥ 50% ⇒ 不是「绕开」而是「处刑」⇒ 归 T6；否则归 T5）；
 * 三者均不成立或 F1/F2 均不成立而 F3 成立 → 按 F3/无判决列出（不编造）。
 */
function decide(a: Agg): {
  branch: string
  why: string
  metrics: Record<string, number>
  presence: Record<string, boolean>
} {
  const { rate, share, conv } = hitRates(a)
  const powerIdx = KINDS.indexOf('power')
  const A = a.deaths > 0 ? a.killerByKind.power / a.deaths : 0
  const hitGames = a.games - a.firstHitByKind.none
  const B = hitGames > 0 ? 1 - a.firstHitByKind.power / hitGames : 0
  const C =
    a.powerPresentGames > 0 ? (a.powerKilledLast + a.powerNeverKilled) / a.powerPresentGames : 0
  const S = share[powerIdx]
  const E = conv[powerIdx]
  // 条件化后的 power 命中率（④ 的分母不变，只是只算败局/胜局子集）。
  const rateIn = (c: CondAgg, i: number): number =>
    c.exposure[i] > 0 ? (1000 * c.hits[i]) / c.exposure[i] : 0
  const metrics = {
    A,
    B,
    C,
    S,
    E,
    firstHitPowerShare: hitGames > 0 ? a.firstHitByKind.power / hitGames : 0,
    lostPowerRate: rateIn(a.lost, powerIdx),
    lostBasicRate: rateIn(a.lost, 0),
    wonPowerRate: rateIn(a.won, powerIdx),
    powerRate: 1000 * rate[powerIdx],
  }
  const presence = { F1: A >= 0.5, F2: B >= 0.6 && C >= 0.6, F3: E <= 0.4 && S >= 0.2 }
  const tie = (): { branch: string; why: string } => {
    const fp = metrics.firstHitPowerShare
    return fp >= 0.5
      ? {
          branch: 'T6（处刑以 power 快弹为主）',
          why: `tie-break：power 占首命中 ${pct(fp)} ≥ 50% ⇒ 不是绕开而是处刑`,
        }
      : {
          branch: 'T5（系统性绕开 power）',
          why: `tie-break：power 占首命中仅 ${pct(fp)} < 50% ⇒ 归属「绕开」分支`,
        }
  }
  if (presence.F1 && presence.F2) {
    const t = tie()
    return {
      branch: t.branch,
      why: `F1（①处刑，A=${A.toFixed(3)}）与 F2（②绕开，B=${B.toFixed(3)}、C=${C.toFixed(3)}）同时成立 ⇒ ${t.why}`,
      metrics,
      presence,
    }
  }
  if (presence.F1)
    return {
      branch: 'T6（处刑以 power 快弹为主）',
      why: `F1: ① killer=power 份额 A=${A.toFixed(3)} ≥ 0.50（F2 不成立）`,
      metrics,
      presence,
    }
  if (presence.F2)
    return {
      branch: 'T5（系统性绕开 power）',
      why: `F2: 首命中非 power B=${B.toFixed(3)} ≥ 0.60 且 power 存活最久 C=${C.toFixed(3)} ≥ 0.60`,
      metrics,
      presence,
    }
  if (presence.F3)
    return {
      branch: '生存/执行分支（打得中、打不死）',
      why: `F3: power 转化 E=${E.toFixed(3)} ≤ 0.40 且归一命中份额 S=${S.toFixed(3)} ≥ 0.20`,
      metrics,
      presence,
    }
  return {
    branch: '无分支压倒性成立（需人工裁量，非 T5/T6 二选一）',
    why: 'F1/F2/F3 全部不成立',
    metrics,
    presence,
  }
}

function report(a: Agg): string {
  const L: string[] = []
  const P = (s: string) => L.push(s)
  const { rate, share, conv } = hitRates(a)
  const dec = decide(a)
  const powerIdx = KINDS.indexOf('power')
  const hitGames = a.games - a.firstHitByKind.none
  const killGames = a.games - a.firstKillByKind.none

  P(`## ${a.label}`)
  P('')
  P(
    `局数 ${a.games}（胜 ${a.wins} = ${pct(a.wins / Math.max(1, a.games))}）；` +
      `死亡局 ${a.deathGames}（${pct(a.deathGames / Math.max(1, a.games))}），死亡次数 ${a.deaths}`,
  )
  P('')
  // ①
  P('### ① killer-kind —— 谁杀了我（主判据）')
  P('')
  P('| killer | 死亡次数 | 份额 (Wilson 95%) |')
  P('|---|---|---|')
  for (const k of KINDS) {
    P(`| ${k} | ${a.killerByKind[k]} | ${pctCi(a.killerByKind[k], a.deaths)} |`)
  }
  if (a.killerUnknown > 0)
    P(`| (unknown) | ${a.killerUnknown} | ${pctCi(a.killerUnknown, a.deaths)} |`)
  P('')
  // ②
  P('### ② 首命中 / 首杀目标 kind —— 先打谁')
  P('')
  P(
    '| 目标 | 首命中局数 (有命中局 n=' +
      hitGames +
      ') | 份额 | 首杀局数 (有击杀局 n=' +
      killGames +
      ') | 份额 |',
  )
  P('|---|---|---|---|---|')
  for (const k of KINDS) {
    P(
      `| ${k} | ${a.firstHitByKind[k]} | ${pctCi(a.firstHitByKind[k], hitGames)} | ` +
        `${a.firstKillByKind[k]} | ${pctCi(a.firstKillByKind[k], killGames)} |`,
    )
  }
  P(`| (无) | ${a.firstHitByKind.none} | — | ${a.firstKillByKind.none} | — |`)
  P('')
  // ③
  P('### ③ 击杀顺序 —— power 是否恒死最后')
  P('')
  P(
    `power 在场局 ${a.powerPresentGames}：**从没被杀 ${a.powerNeverKilled}**（` +
      `${pctCi(a.powerNeverKilled, a.powerPresentGames)}）、` +
      `**最后才被杀 ${a.powerKilledLast}**（${pctCi(a.powerKilledLast, a.powerPresentGames)}）、` +
      `被杀的局 ${a.powerKillPosN}` +
      (a.powerKillPosN > 0
        ? `，平均击杀位次 ${(a.powerKillPosSum / a.powerKillPosN).toFixed(2)}`
        : ''),
  )
  P('')
  // ④
  P('### ④ 曝光归一命中份额（分母 = 存活×接战 tick 积分；禁用「在场份额」）')
  P('')
  P('| kind | 曝光(敌车tick) | 命中 | 击杀 | 命中/千tick | 归一份额 | 命中→击杀 |')
  P('|---|---|---|---|---|---|---|')
  for (let i = 0; i < 4; i++) {
    P(
      `| ${KINDS[i]} | ${a.exposure[i]} | ${a.hits[i]} | ${a.kills[i]} | ` +
        `${(1000 * rate[i]).toFixed(2)} | ${pct(share[i])} | ` +
        `${a.hits[i] > 0 ? pct(conv[i]) : '—'} |`,
    )
  }
  P('')
  const ts = a.exposure[powerIdx] > 0 ? Math.sqrt(a.hits[powerIdx]) / a.exposure[powerIdx] : 0
  P(
    `power 命中率 ${(1000 * rate[powerIdx]).toFixed(2)}/千tick（泊松 95% ≈ ±` +
      `${(1000 * 1.96 * ts).toFixed(2)}）；命中 ${a.hits[powerIdx]} / 击杀 ${a.kills[powerIdx]} ⇒ 转化 ` +
      `${a.hits[powerIdx] > 0 ? pct(conv[powerIdx]) : '—'}`,
  )
  P('')
  // 胜/败条件化（③ 不条件化必被「败局当然没打完」混淆）
  const pi = powerIdx
  const rIn = (c: CondAgg, i: number): string =>
    (c.exposure[i] > 0 ? (1000 * c.hits[i]) / c.exposure[i] : 0).toFixed(2)
  P('### 胜/败条件化 —— ③ 的混淆校正（**必读**）')
  P('')
  P(
    '| 子集 | 局 | power 在场局 | 其中命中过 power | 其中杀掉 power | power 占首命中 | power 命中/千tick | basic 命中/千tick |',
  )
  P('|---|---|---|---|---|---|---|---|')
  for (const [name, c] of [
    ['胜局', a.won],
    ['败局', a.lost],
  ] as const) {
    P(
      `| ${name} | ${c.games} | ${c.powerPresent} | ${c.powerHit}（${pctCi(c.powerHit, c.powerPresent)}） | ` +
        `${c.powerKilled}（${pctCi(c.powerKilled, c.powerPresent)}） | ${c.firstHitPower} | ` +
        `${rIn(c, pi)} | ${rIn(c, 0)} |`,
    )
  }
  P('')
  P(
    '（分母为各子集自己的曝光积分；败局列的「power 在场局里没碰过 power」是「绕开」的直接证据，' +
      '而胜局列说明 NN **有能力**打掉 power ⇒ ③ 的「从未被杀」主要是「败局没打完」的产物，不是能力缺失。）',
  )
  P('')
  // 判决
  P(`### 分支判决：${dec.branch}`)
  P('')
  P(`- 依据：${dec.why}`)
  P(
    `- 输入量：A=${dec.metrics.A.toFixed(3)}（① power 凶手份额）、B=${dec.metrics.B.toFixed(3)}` +
      `（② 首命中非 power）、C=${dec.metrics.C.toFixed(3)}（③ power 存活最久）、` +
      `S=${dec.metrics.S.toFixed(3)}（④ power 归一命中份额）、E=${dec.metrics.E.toFixed(3)}（④ power 转化率）、` +
      `首命中 power 份额=${pct(dec.metrics.firstHitPowerShare)}`,
  )
  P(
    `- 条件化佐证：败局 power 命中率 ${dec.metrics.lostPowerRate.toFixed(2)}/千tick（basic ` +
      `${dec.metrics.lostBasicRate.toFixed(2)}）vs 胜局 power ${dec.metrics.wonPowerRate.toFixed(2)}/千tick`,
  )
  P(
    `- 分支成立向量：F1(①处刑)=${dec.presence.F1}、F2(②绕开)=${dec.presence.F2}、F3(③打不死)=${dec.presence.F3}`,
  )
  P(
    '- 规则（预注册）：F1 单独→T6；F2 单独→T5；F3 单独→生存/执行；**F1∧F2 同时成立→tie-break 看 ② 首命中分布**',
  )
  P('')
  // 样本声明
  const thin: string[] = []
  if (a.deathGames < 30) thin.push(`死亡局 ${a.deathGames} < 30`)
  if (a.killerByKind.power < 30) thin.push(`① power 凶手 ${a.killerByKind.power} < 30`)
  if (hitGames < 30) thin.push(`有命中局 ${hitGames} < 30`)
  if (a.powerPresentGames < 30) thin.push(`power 在场局 ${a.powerPresentGames} < 30`)
  P(
    `**样本声明**：${thin.length === 0 ? '各指纹样本量均 ≥30，无薄样本告警。' : `薄样本（结论按方向读、不按点估计读）：${thin.join('；')}`}`,
  )
  P('')
  return L.join('\n')
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

function argAll(name: string): string[] {
  const out: string[] = []
  for (let i = 0; i < process.argv.length; i++) {
    if (process.argv[i] === `--${name}` && i + 1 < process.argv.length)
      out.push(process.argv[i + 1])
  }
  return out
}

function main(): void {
  const inputs = argAll('in')
  if (inputs.length === 0) {
    console.error('[phase0] --in [label=]<rows.jsonl> required (repeatable)')
    process.exit(2)
  }
  const parts: string[] = []
  parts.push('# Phase 0 逐敌种画像（T3）')
  parts.push('')
  parts.push(
    '四指纹 + 预注册分支判决。口径冻结于 `plan/x3-power-followup.plan.md` §T3；' +
      '曝光分母 = `exposureByKind`（存活×接战 tick），**非**在场份额。',
  )
  parts.push('')
  for (const spec of inputs) {
    const eq = spec.indexOf('=')
    const label = eq > 0 ? spec.slice(0, eq) : spec
    const path = eq > 0 ? spec.slice(eq + 1) : spec
    const rows = readFileSync(path, 'utf8')
      .split(/\r?\n/)
      .filter((l) => l.trim() !== '')
      .map((l) => JSON.parse(l) as Row)
    const byStage = new Map<number, Agg>()
    const pooled = newAgg(label)
    for (const r of rows) {
      accumulate(pooled, r)
      let s = byStage.get(r.stageId)
      if (!s) {
        s = newAgg(`${label} · stage${r.stageId} (${r.stageName ?? ''})`)
        byStage.set(r.stageId, s)
      }
      accumulate(s, r)
    }
    parts.push(report(pooled))
    parts.push('### 分关（只诊断，不进判据）')
    parts.push('')
    parts.push('| 关 | 局 | 胜 | ① power 凶手 | ② 首命中 power | ③ power 没被杀 | ④ power 转化 |')
    parts.push('|---|---|---|---|---|---|---|')
    for (const s of byStage.values()) {
      const { conv } = hitRates(s)
      const pIdx = KINDS.indexOf('power')
      const hitGames = s.games - s.firstHitByKind.none
      parts.push(
        `| ${s.label.split(' · ')[1]} | ${s.games} | ${s.wins} | ` +
          `${s.deaths > 0 ? pct(s.killerByKind.power / s.deaths) : '—'} | ` +
          `${hitGames > 0 ? pct(s.firstHitByKind.power / hitGames) : '—'} | ` +
          `${s.powerPresentGames > 0 ? pct((s.powerNeverKilled + s.powerKilledLast) / s.powerPresentGames) : '—'} | ` +
          `${s.hits[pIdx] > 0 ? pct(conv[pIdx]) : '—'} |`,
      )
    }
    parts.push('')
  }
  const text = parts.join('\n')
  const out = arg('out')
  if (out) {
    mkdirSync(dirname(out), { recursive: true })
    writeFileSync(out, text)
    console.error(`[phase0] report -> ${out}`)
  }
  process.stdout.write(text)
}

if (import.meta.main) main()
