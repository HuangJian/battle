/**
 * report.ts — Statistics aggregation and reporting.
 *
 * Takes batch simulation results and produces human-readable reports
 * with pass rates, percentile times, metric distributions, and per-stage
 * summaries (plan/Automated-Level-Design §Phase 3a.2).
 */

import type { BatchResult, BatchSummary } from './batch-sim'
import { summarize } from './batch-sim'

// ============================================================
// Types
// ============================================================

export interface PerStageReport {
  stageIndex: number
  stageName: string
  runs: number
  outcomes: Record<string, number>
  passRate: number
  avgPlayTimeMs: number
  p90PlayTimeMs: number
  avgScore: number
}

export interface MetricDistribution {
  name: string
  mean: number
  min: number
  max: number
  p25: number
  p50: number
  p75: number
}

export interface Report {
  summary: BatchSummary
  perStage: PerStageReport[]
  metricDistributions: MetricDistribution[]
}

// ============================================================
// Report generation
// ============================================================

export function generateReport(results: BatchResult[]): Report {
  const summary = summarize(results)

  // Per-stage breakdown
  const stageMap = new Map<number, BatchResult[]>()
  for (const r of results) {
    if (!stageMap.has(r.stageIndex)) stageMap.set(r.stageIndex, [])
    stageMap.get(r.stageIndex)!.push(r)
  }

  const perStage: PerStageReport[] = []
  for (const [idx, stageResults] of stageMap) {
    const stageSummary = summarize(stageResults)
    perStage.push({
      stageIndex: idx,
      stageName: stageResults[0]?.stageName ?? `Stage ${idx}`,
      runs: stageResults.length,
      outcomes: stageSummary.outcomes,
      passRate: stageSummary.passRate,
      avgPlayTimeMs: stageSummary.avgPlayTimeMs,
      p90PlayTimeMs: stageSummary.p90PlayTimeMs,
      avgScore: stageSummary.avgScore,
    })
  }
  perStage.sort((a, b) => a.stageIndex - b.stageIndex)

  // Metric distributions
  const metricDistributions: MetricDistribution[] = Object.entries(summary.metricStats).map(
    ([name, stats]) => ({ name, ...stats }),
  )

  return { summary, perStage, metricDistributions }
}

/**
 * Format a report as human-readable text.
 */
export function formatReport(report: Report): string {
  const lines: string[] = []
  const s = report.summary

  lines.push('=== Batch Simulation Report ===')
  lines.push('')
  lines.push(`Total runs: ${s.totalRuns}`)
  lines.push(`Outcomes: ${JSON.stringify(s.outcomes)}`)
  lines.push(`Pass rate: ${(s.passRate * 100).toFixed(1)}%`)
  lines.push(`Avg play time: ${(s.avgPlayTimeMs / 1000).toFixed(1)}s`)
  lines.push(`P50 play time: ${(s.p50PlayTimeMs / 1000).toFixed(1)}s`)
  lines.push(`P90 play time: ${(s.p90PlayTimeMs / 1000).toFixed(1)}s`)
  lines.push(`Avg score: ${s.avgScore.toFixed(0)}`)
  lines.push(`Avg kills: ${s.avgKills.toFixed(1)}`)
  lines.push(`Avg lives remaining: ${s.avgLivesRemaining.toFixed(1)}`)

  if (report.metricDistributions.length > 0) {
    lines.push('')
    lines.push('--- Metric Distributions ---')
    for (const m of report.metricDistributions) {
      lines.push(
        `  ${m.name}: mean=${m.mean.toFixed(2)} p25=${m.p25.toFixed(2)} p50=${m.p50.toFixed(2)} p75=${m.p75.toFixed(2)}`,
      )
    }
  }

  if (report.perStage.length > 1) {
    lines.push('')
    lines.push('--- Per-Stage Summary ---')
    for (const ps of report.perStage) {
      lines.push(
        `  ${ps.stageName}: runs=${ps.runs} pass=${(ps.passRate * 100).toFixed(0)}% avg=${(ps.avgPlayTimeMs / 1000).toFixed(1)}s p90=${(ps.p90PlayTimeMs / 1000).toFixed(1)}s`,
      )
    }
  }

  return lines.join('\n')
}
