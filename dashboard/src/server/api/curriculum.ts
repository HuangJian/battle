/** curriculum.ts — 课程梯度账本读取与阶梯视图。 */
import { readFileSync } from 'fs'
import path from 'path'
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
    // LEDGER 由程序生成（纯 JSON）；容错剥掉潜在注释行后解析。
    const cleaned = raw
      .split('\n')
      .filter((l) => !l.trim().startsWith('//'))
      .join('\n')
    const data = JSON.parse(cleaned) as {
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
