/**
 * probe.ts — W5 探测模式：把「页面到底长什么样」压成一行可上报的事实。
 *
 * 只读、不改 DOM、不抛；URL 一律去掉 query/hash（`?session=` 是 JWT，绝不能进日志）。
 */
import type { Role } from './selectors'
import { detectRole, SEL } from './selectors'

export interface ProbeReport {
  v: 1
  at: number
  /** origin + pathname（无 query/hash）。 */
  href: string
  role: Role | null
  ready: boolean
  cells: number
  codeCells: number
  outputAreas: number
  hasScrollOuter: boolean
  hasCommandMenu: boolean
}

/** origin + pathname；非 URL ⇒ 空串（不把原串当日志）。 */
export function stripUrl(raw: string): string {
  try {
    const u = new URL(raw)
    return `${u.origin}${u.pathname}`
  } catch {
    return ''
  }
}

export function collectProbe(doc: Document | null, href: string, at = Date.now()): ProbeReport {
  const report: ProbeReport = {
    v: 1,
    at,
    href: stripUrl(href),
    role: detectRole(href),
    ready: false,
    cells: 0,
    codeCells: 0,
    outputAreas: 0,
    hasScrollOuter: false,
    hasCommandMenu: false,
  }
  if (!doc || typeof doc.querySelectorAll !== 'function') return report
  try {
    report.cells = doc.querySelectorAll(SEL.cell).length
    report.codeCells = doc.querySelectorAll(SEL.codeCell).length
    report.outputAreas = doc.querySelectorAll(SEL.outputArea).length
    report.hasScrollOuter = doc.querySelector(SEL.scrollOuter) !== null
    report.hasCommandMenu = doc.querySelector('[data-command]') !== null
    report.ready = doc.querySelector(SEL.notebook) !== null
  } catch {
    /* 页面在导航中：返回已收集到的部分，不抛 */
  }
  return report
}

/** 一行日志（GM 控制台/`console.info` 用）；不含任何 query。 */
export function probeLine(r: ProbeReport): string {
  return `[probe] role=${r.role ?? 'none'} ready=${r.ready} cells=${r.cells} code=${r.codeCells} out=${r.outputAreas} scroll=${r.hasScrollOuter} cmd=${r.hasCommandMenu} href=${r.href}`
}
