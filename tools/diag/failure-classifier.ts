/**
 * failure-classifier.ts — M0 failure attribution (plan/
 * God-AI-Hard-Breakthrough-Implementation.md §4.2).
 *
 * Classifies a failed run into one primary + optional secondary failure
 * families, with per-sample evidence strings so every classification is
 * auditable ("抽样逐 tick 检查分类与事实一致" — Phase 0's completion gate).
 *
 * Pure functions — no I/O, no World writes. Unit-tested in
 * tests/threat-ledger.test.ts.
 *
 * The 7 families (plan §4.2):
 *   1. late_detection        — base entered the danger window before the
 *                              threat was ever detected (no shoot-capable
 *                              enemy observed before the first base hit).
 *   2. wrong_target          — threat detected but the player committed to
 *                              an action whose target is not the base threat.
 *   3. travel_late           — target correct but player ETA > enemy deadline.
 *   4. turn_locked           — route correct but the player is locked
 *                              (cooldown / standing) at the critical moment.
 *   5. no_output_commit      — branch committed for consecutive samples with
 *                              no movement, no fire, no kill.
 *   6. multi_threat_overload — one action cannot suppress ≥2 simultaneous
 *                              base threats.
 *   7. player_survival       — base safe; the player's deaths ended the run.
 */
import type { ThreatLedgerRun, ThreatLedgerSample, ForensicsSnapshot } from '../sim/simulation-runner'

export type FailureClass =
  | 'late_detection'
  | 'wrong_target'
  | 'travel_late'
  | 'turn_locked'
  | 'no_output_commit'
  | 'multi_threat_overload'
  | 'player_survival'
  | 'unknown'

export interface Classification {
  primary: FailureClass
  secondary: FailureClass[]
  /** Human-readable evidence lines: which samples/ticks support the verdict. */
  evidence: string[]
}

export const FAILURE_CLASS_NAMES: Record<FailureClass, string> = {
  late_detection: '检测晚',
  wrong_target: '选靶错',
  travel_late: '赶路慢',
  turn_locked: '转向锁定',
  no_output_commit: '无产出提交',
  multi_threat_overload: '多威胁过载',
  player_survival: '玩家生存',
  unknown: '未知',
}

/** Branches that are offense/roaming — committing them while a base threat
 *  exists and the player is moving = actively doing something else. */
const OFFENSE_BRANCHES = new Set([
  'engage',
  'hunt',
  'aggro',
  'aggressive',
  'pickupHigh',
  'pickupMid',
  'pickupLow',
  'closePickup',
  'firingLane',
  'carvePath',
  'baseConnectClear',
  'midLaneHold',
  'powerup',
])

/** Branches that are defense/nav — heading toward the threat. */
const DEFENSE_BRANCHES = new Set([
  'defenseIntercept',
  'midLaneDefense',
  'baseLaneSentry',
  'navigate',
  't8',
  'interceptBase',
  // M4 unified action candidates (§7) — all four are threat-response
  // commits, never wrong_target.
  'candidateKill',
  'candidateIntercept',
  'candidateClear',
  'candidateReturn',
])

/** Minimum consecutive no-output samples for a no_output_commit verdict. */
export const NO_OUTPUT_MIN_SAMPLES = 3

/** Is there any shoot-capable (csb/cbr) enemy in this sample? */
export function hasThreatEnemy(s: ThreatLedgerSample): boolean {
  for (let i = 0; i < s.enemies.length; i++) {
    if (s.enemies[i].canShootBase || s.enemies[i].canBreachRing) return true
  }
  return false
}

/** Count of simultaneous shoot-capable enemies in this sample. */
export function threatEnemyCount(s: ThreatLedgerSample): number {
  let n = 0
  for (let i = 0; i < s.enemies.length; i++) {
    if (s.enemies[i].canShootBase || s.enemies[i].canBreachRing) n++
  }
  return n
}

/** Is the player standing with no movement AND no fire (a no-output commit)? */
export function isNoOp(s: ThreatLedgerSample): boolean {
  return s.noOpReason !== null
}

/** Is the player aligned (same row or column) with a csb enemy? */
function alignedWithCsb(s: ThreatLedgerSample): boolean {
  for (let i = 0; i < s.enemies.length; i++) {
    const e = s.enemies[i]
    if (!e.canShootBase) continue
    if (e.cell.col === s.playerCell.col || e.cell.row === s.playerCell.row) return true
  }
  return false
}

/**
 * Classify a failed run. `cause` is the failure taxonomy cause
 * ('base_destroyed' | 'lives_exhausted' | 'timeout').
 */
export function classifyFailure(
  ledger: ThreatLedgerRun,
  cause: string | undefined,
  terminal?: ForensicsSnapshot,
): Classification {
  const evidence: string[] = []
  const secondary: FailureClass[] = []
  const samples = ledger.samples
  const baseMaxHp = ledger.baseMaxHp

  // ---- player_survival: the base was never the losing factor ----
  if (cause === 'lives_exhausted') {
    const baseEverHit = samples.some((s) => s.baseHp < baseMaxHp)
    evidence.push(
      `cause=lives_exhausted; base ever hit: ${baseEverHit ? 'yes' : 'no'} (terminal baseHp=${terminal?.baseHp ?? '?'}/${baseMaxHp})`,
    )
    if (baseEverHit) secondary.push('multi_threat_overload')
    return { primary: 'player_survival', secondary, evidence }
  }
  if (cause === 'timeout') {
    evidence.push(`cause=timeout; run ended at max ticks without a terminal state`)
    return { primary: 'unknown', secondary, evidence }
  }

  // ---- base_destroyed ----
  const firstHitIdx = samples.findIndex((s) => s.baseHp < baseMaxHp)
  const firstDangerIdx = samples.findIndex(hasThreatEnemy)
  // The "active threat" window also includes the AI's own baseThreatNow signal
  // (a standing no-op only matters when the base is actually under pressure,
  // whether or not an enemy has reached a shoot position yet).
  const firstActiveIdx = samples.findIndex((s) => hasThreatEnemy(s) || s.baseThreatNow)

  // 1. late_detection: the base was hit before any shoot-capable enemy was
  //    observed (the danger window opened unnoticed).
  if (firstHitIdx >= 0 && (firstDangerIdx < 0 || firstDangerIdx > firstHitIdx)) {
    evidence.push(
      `late_detection: first base hit at t=${samples[firstHitIdx].tick}` +
        ` (baseHp ${baseMaxHp}→${samples[firstHitIdx].baseHp}),` +
        ` but no sample before that had any csb/cbr enemy` +
        (firstDangerIdx >= 0 ? ` (first danger sample only at t=${samples[firstDangerIdx].tick})` : ` (never)`) +
        `; branch at hit=${samples[firstHitIdx].branch}, playerDist=${Math.abs(
          samples[firstHitIdx].playerCell.col - 12,
        ) + Math.abs(samples[firstHitIdx].playerCell.row - 24)}`,
    )
    return { primary: 'late_detection', secondary, evidence }
  }

  // The danger window = everything from the first active-threat sample onward.
  const window = firstActiveIdx >= 0 ? samples.slice(firstActiveIdx) : samples

  // 2. no_output_commit: consecutive no-op samples within the window.
  let noOpRun = 0
  for (let i = 0; i < window.length; i++) {
    if (isNoOp(window[i])) {
      noOpRun++
      if (noOpRun >= NO_OUTPUT_MIN_SAMPLES) {
        const from = window[i - NO_OUTPUT_MIN_SAMPLES + 1]
        evidence.push(
          `no_output_commit: ${NO_OUTPUT_MIN_SAMPLES}+ consecutive no-op samples` +
            ` (t=${from.tick}..${window[i].tick}, branch=${from.noOpReason ?? '?'}),` +
            ` player stuck at (${from.playerCell.col},${from.playerCell.row}) with no move/fire` +
            ` while base threat active; slack=${window[i].threatSlack}`,
        )
        secondary.push('travel_late')
        return { primary: 'no_output_commit', secondary, evidence }
      }
    } else {
      noOpRun = 0
    }
  }

  // 3. multi_threat_overload: ≥2 simultaneous shoot-capable enemies.
  for (let i = 0; i < window.length; i++) {
    const n = threatEnemyCount(window[i])
    if (n >= 2) {
      evidence.push(
        `multi_threat_overload: t=${window[i].tick} has ${n} simultaneous shoot-capable enemies` +
          ` (${window[i].enemies
            .filter((e) => e.canShootBase || e.canBreachRing)
            .map((e) => `#${e.id}${e.canShootBase ? '→base' : '→ring'}`)
            .join(',')}); player branch=${window[i].branch}, slack=${window[i].threatSlack}`,
      )
      return { primary: 'multi_threat_overload', secondary, evidence }
    }
  }

  // 4. turn_locked: aligned with a csb enemy but standing on cooldown —
  //    the shot/turn could not legally happen in time.
  for (let i = 0; i < window.length; i++) {
    const s = window[i]
    if (alignedWithCsb(s) && s.onCooldown && s.noOpReason !== null) {
      evidence.push(
        `turn_locked: t=${s.tick} player aligned with a csb enemy, onCooldown=${s.onCooldown},` +
          ` standing (noOp reason=${s.noOpReason}), branch=${s.branch}, slack=${s.threatSlack}`,
      )
      return { primary: 'turn_locked', secondary, evidence }
    }
  }

  // 5. travel_late: the player's intercept ETA exceeded the threat deadline.
  for (let i = 0; i < window.length; i++) {
    const s = window[i]
    if (s.threatSlack < 0 && s.playerEtaToBestIntercept >= 0 && s.nearestThreatEta >= 0) {
      evidence.push(
        `travel_late: t=${s.tick} nearestThreatEta=${s.nearestThreatEta}` +
          ` > playerEtaToBestIntercept=${s.playerEtaToBestIntercept}` +
          ` (slack=${s.threatSlack}), branch=${s.branch}, player at` +
          ` (${s.playerCell.col},${s.playerCell.row})`,
      )
      return { primary: 'travel_late', secondary, evidence }
    }
  }

  // 6. wrong_target: offense/roaming branch with movement while threatened.
  for (let i = 0; i < window.length; i++) {
    const s = window[i]
    if (OFFENSE_BRANCHES.has(s.branch) && s.noOpReason === null) {
      evidence.push(
        `wrong_target: t=${s.tick} branch=${s.branch} while base threat active` +
          ` (threats=${threatEnemyCount(s)}), player at (${s.playerCell.col},${s.playerCell.row}),` +
          ` slack=${s.threatSlack}`,
      )
      return { primary: 'wrong_target', secondary, evidence }
    }
  }

  // 7. Fallback: the window was spent in defense branches but still lost.
  if (window.length > 0) {
    const last = window[window.length - 1]
    const branchMix = [...new Set(window.map((s) => s.branch))].join(',')
    evidence.push(
      `unknown: danger window t=${window[0].tick}..${last.tick} spent in branches [${branchMix}],` +
        ` final slack=${last.threatSlack}, liveEnemies=${last.liveEnemies},` +
        ` firstHit=${firstHitIdx >= 0 ? `t=${samples[firstHitIdx].tick}` : 'never'} —` +
        ` no single-family evidence (M1 slack model should refine)`,
    )
    return { primary: 'unknown', secondary, evidence }
  }

  evidence.push(`unknown: no danger window at all (no csb/cbr enemy ever observed)`)
  return { primary: 'unknown', secondary, evidence }
}

/** Aggregate classifications across runs → per-family counts (for reports). */
export function aggregateClassifications(
  classified: Array<{ key: string; classification: Classification }>,
): Map<FailureClass, number> {
  const out = new Map<FailureClass, number>()
  for (const { classification } of classified) {
    out.set(classification.primary, (out.get(classification.primary) ?? 0) + 1)
  }
  return out
}

/** True when this is a defense-family branch (used by reports/spot-checks). */
export function isDefenseBranch(branch: string): boolean {
  return DEFENSE_BRANCHES.has(branch)
}
