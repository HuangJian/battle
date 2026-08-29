/**
 * batch-ledger.ts — 跑批逐局账本：断点续跑 + 错误局重跑（rollout 机制的 TS 提取）。
 *
 * 对齐 nn-training/rl/resume.py 的语义（completed_pairs + wver 匹配 + 跨进程续跑），
 * 供 m1-eval 及后续 TS 跑批工具复用（rollout 侧 python 已有同名机制——见
 * docs/goal-nn.progress.md §6 的能力映射表）。
 *
 * 形态：append-only jsonl，每行一局。判定"已完成"= ok=true 且 wver 匹配当前批
 * （权重/代码版本变化 → 旧结果不失效文件但也不计入 done —— 与 resume.py 的
 * manifest.wver 过滤同口径）。ok=false 的行留在文件里作审计，但不算完成；
 * 调用方的错误重跑会追加新行，后写覆盖先读（loadDone 取每 key 的最后一次）。
 *
 * 确定性护栏：结果按 (stage,seed) 与 wver 记账；跨跑批可配对的前提是
 * wver 覆盖"影响结果的全部输入"（权重 + 策略版本）。代码变更需 --fresh 重跑。
 */

import { appendFileSync, existsSync, readFileSync } from 'node:fs'

export interface LedgerEntry {
  wver: string
  stage: number
  seed: number
  ok: boolean
  outcome: string
  ticks: number
  killCount: number
  baseAlive: boolean
  ts: number
}

export function ledgerKey(stage: number, seed: number): string {
  return `${stage}:${seed}`
}

export class BatchLedger {
  readonly path: string
  private readonly wver: string
  private loaded = false
  private readonly done = new Map<string, LedgerEntry>()

  constructor(path: string, wver: string) {
    this.path = path
    this.wver = wver
  }

  /** 启动时扫描账本，返回当前 wver 下已完成 (stage,seed) 集合。 */
  loadDone(): Map<string, LedgerEntry> {
    if (this.loaded) return this.done
    this.loaded = true
    if (!existsSync(this.path)) return this.done
    const lines = readFileSync(this.path, 'utf8').split('\n')
    for (const line of lines) {
      if (!line.trim()) continue
      try {
        const e = JSON.parse(line) as LedgerEntry
        if (e.wver !== this.wver || !e.ok) continue
        if (!Number.isInteger(e.stage) || !Number.isInteger(e.seed)) continue
        // 后写覆盖先读（错误重跑的追加行生效）
        this.done.set(ledgerKey(e.stage, e.seed), e)
      } catch {
        /* 坏行跳过（与 resume.py 同容错） */
      }
    }
    return this.done
  }

  /** 结算一局（本地或远端）后追加。同步 append——跑批频率低，无热路径问题。 */
  append(e: Omit<LedgerEntry, 'ts'>): void {
    const line = JSON.stringify({ ...e, ts: Date.now() })
    appendFileSync(this.path, line + '\n', 'utf8')
    if (e.ok && e.wver === this.wver) {
      this.done.set(ledgerKey(e.stage, e.seed), { ...e, ts: Date.now() })
    }
  }
}
