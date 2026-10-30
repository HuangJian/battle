import { describe, expect, it } from 'bun:test'
import {
  dueOccurrence,
  nextFireAt,
  normalizeAt,
  type DailyJob,
  type LocalParts,
} from '../src/notebook-companion/schedule'

/** 假纽约时区：2026-03-08 07:00Z 春进（02:00→03:00），2026-11-01 06:00Z 秋退（02:00→01:00）。 */
const SPRING = Date.UTC(2026, 2, 8, 7)
const FALL = Date.UTC(2026, 10, 1, 6)
const p2 = (n: number): string => String(n).padStart(2, '0')
function ny(ms: number): LocalParts {
  const offset = ms < SPRING || ms >= FALL ? -5 : -4
  const d = new Date(ms + offset * 3_600_000)
  return {
    date: `${d.getUTCFullYear()}-${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`,
    time: `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`,
    weekday: d.getUTCDay(),
  }
}

const job = (over: Partial<DailyJob> = {}): DailyJob => ({
  id: 'nightly',
  at: '10:30',
  days: null,
  catchUp: false,
  enabled: true,
  ...over,
})

describe('定时：下一次 / 欠不欠（DST 用真实分钟扫描）', () => {
  it('普通日：下一次 = 今天目标时刻；已过则明天', () => {
    const fri = Date.UTC(2026, 9, 30, 9, 0) // 纽约周五 05:00
    expect(nextFireAt(job(), fri, ny)).toBe(Date.UTC(2026, 9, 30, 14, 30)) // 10:30 EDT
    const after = Date.UTC(2026, 9, 30, 15, 0) // 11:00 EDT
    expect(nextFireAt(job(), after, ny)).toBe(Date.UTC(2026, 9, 31, 14, 30))
  })

  it('days 过滤：周五只有周一的点 ⇒ 下周一（跨秋退后 UTC 15:30）', () => {
    const fri = Date.UTC(2026, 9, 30, 9, 0)
    expect(nextFireAt(job({ days: [1] }), fri, ny)).toBe(Date.UTC(2026, 10, 2, 15, 30))
  })

  it('春进：02:30 当天不存在 ⇒ 顺延到次日（不提前到 01:30/03:30）', () => {
    const before = Date.UTC(2026, 2, 8, 6, 0) // 纽约 01:00 EST
    expect(nextFireAt(job({ at: '02:30' }), before, ny)).toBe(Date.UTC(2026, 2, 9, 6, 30))
  })

  it('秋退：01:30 出现两次，同一个 occurrenceKey 只欠一次', () => {
    const first = Date.UTC(2026, 10, 1, 5, 30) // 01:30 EDT
    const second = Date.UTC(2026, 10, 1, 6, 30) // 01:30 EST（回拨后）
    const a = dueOccurrence(job({ at: '01:30' }), first, null, ny)
    expect(a?.key).toBe('2026-11-01@01:30')
    expect(dueOccurrence(job({ at: '01:30' }), second, a!.key, ny)).toBeNull()
  })

  it('catchUp=false 错过 >2 分钟不补；catchUp=true 补', () => {
    const at = Date.UTC(2026, 9, 30, 14, 30) // 10:30 EDT
    expect(dueOccurrence(job(), at + 5 * 60_000, null, ny)).toBeNull()
    expect(dueOccurrence(job({ catchUp: true }), at + 5 * 60_000, null, ny)?.at).toBe(at)
  })

  it('禁用 / 非法时间 ⇒ null；normalizeAt 补零并拒绝越界', () => {
    expect(nextFireAt(job({ enabled: false }), Date.UTC(2026, 9, 30), ny)).toBeNull()
    expect(nextFireAt(job({ at: '24:00' }), Date.UTC(2026, 9, 30), ny)).toBeNull()
    expect(normalizeAt('9:05')).toBe('09:05')
    expect(normalizeAt('12:60')).toBeNull()
  })
})
