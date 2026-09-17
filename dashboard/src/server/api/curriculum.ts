/** curriculum.ts — 课程梯度账本读取与阶梯视图。 */
import { readFileSync } from 'fs'
import path from 'path'
import { parseJsonc } from '../../core/jsonc'
import { NN_TRAINING } from '../../core/paths'

export interface CurriculumLadderLevel {
  level: string
  status: string
  count?: number
  hypothesis?: string
  teacherWR?: number
  lastGate?: { verdict?: string; pooledPassRate?: number; wilsonLB?: number; date?: string }
  diskBytes?: number
  escalateReason?: string
}

export function readCurriculumLedger(
  ledgerPath = path.join(NN_TRAINING, 'ladder', 'LEDGER.jsonc'),
): {
  levels: Record<string, CurriculumLadderLevel>
  updatedAt?: string
} {
  try {
    const raw = readFileSync(ledgerPath, 'utf8')
    // LEDGER 由程序生成（纯 JSON），但解析走共用 JSONC 解析器（core/jsonc.ts，与
    // python `rl/jsonc.py` 同语义）——原先手搓的「只剥整行 `//`」遇行内注释即崩，
    // 与 2026-09-17 课程种子事故同一根因；共用实现接受严格超集，行为只增不减。
    const data = parseJsonc(raw) as {
      levels?: Record<string, Record<string, unknown>>
      updated_at?: string
    }
    const levels: Record<string, CurriculumLadderLevel> = {}
    for (const [k, v] of Object.entries(data.levels ?? {})) {
      levels[k] = {
        level: k,
        status: String(v.status ?? 'pending'),
        count: typeof v.count === 'number' ? v.count : undefined,
        hypothesis: typeof v.hypothesis === 'string' ? v.hypothesis : undefined,
        teacherWR: typeof v.teacherWR === 'number' ? v.teacherWR : undefined,
        lastGate: (v.lastGate as CurriculumLadderLevel['lastGate']) ?? undefined,
        diskBytes: typeof v.disk_bytes === 'number' ? v.disk_bytes : undefined,
        escalateReason: typeof v.escalate_reason === 'string' ? v.escalate_reason : undefined,
      }
    }
    return { levels, updatedAt: data.updated_at }
  } catch {
    // 台账缺席/损坏不挡控制台——返回空视图（LAN 只读，绝不反杀训练）。
    return { levels: {} }
  }
}

export function curriculumLadderView(): {
  levels: CurriculumLadderLevel[]
  updatedAt?: string
} {
  const { levels, updatedAt } = readCurriculumLedger()
  return { levels: Object.values(levels).sort((a, b) => a.level.localeCompare(b.level)), updatedAt }
}
