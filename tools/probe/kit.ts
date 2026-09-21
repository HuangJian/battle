#!/usr/bin/env bun
/**
 * kit.ts — human-opening probe: operator kit (human-opening-probe.plan v7 §T7)
 *
 *   bun tools/probe/kit.ts                        # 准备：7 个可点链接 + 空 verdict 表
 *   bun tools/probe/kit.ts <session-pack.zip>     # 收官：annotate + 填好的 verdict 表 + 校验
 *
 * T7 is the human's step; this tool is the agent's half — it removes every place
 * the operator could transcribe something wrong:
 *
 *   prep — the verdict table is DERIVED from the manifest (game/stage/seed/tag),
 *          so it can never drift from what the browser will actually run, and the
 *          seven launch links are built with the same `probeGameHref` the Control
 *          Center launcher uses.
 *   pack — the annotation is the SHARED `annotatePack` (byte-identical to running
 *          `tools/probe/annotate.ts`), the table is filled from the pack's own
 *          `verdicts.jsonl`, and the seed verdict comes from the ONE band table
 *          (`src/probe/verdict.ts`) — never re-derived here.
 *
 * What this adds over the two underlying tools is REVIEW of the delivered pack:
 *
 *   • `courseSha` — the pack's sha must equal THIS checkout's manifest sha. The
 *     browser cannot check it (the level file is not served); offline we can, and
 *     a mismatch means the operator judged a different build than this repo holds.
 *   • the pack's (game, stage, seed) table must match the manifest.
 *   • captured-but-unjudged / judged-but-uncaptured games are named, not silently
 *     rendered as blank cells.
 *   • a band that contradicts the annotation's own outcome is flagged (a "solvable"
 *     verdict on a run the replay says ended in a death is a data-entry slip).
 *   • the §0 calibration gate: if game 0 does not come out `solvable`, the whole
 *     interpretation is void — said out loud, as an error.
 *
 * Pure core (`inspectPack` / `renderSheet`) + a thin CLI, so the sheet and the
 * checks are unit-tested without a browser.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { annotatePack, type AnnotationLine } from './annotate'
import { parseProbeManifestText, type ProbeManifest } from '../../src/probe/manifest'
import { probeGameHref } from '../../src/probe/query'
import { readStoreZip, type ZipEntry } from '../../src/probe/zip'
import {
  BAND_TABLE,
  ProbeVerdictError,
  aggregateSeedVerdict,
  validateProbeVerdict,
  verdictOfBand,
  type ProbeBand,
  type ProbeVerdict,
  type SeedVerdict,
} from '../../src/probe/verdict'

const DEFAULT_MANIFEST = 'public/probe/x20-opening.json'
/** Vite dev server — keep in sync with `vite.config.ts` (`server.port`). */
const DEFAULT_BASE = 'http://localhost:8956'
const DEFAULT_OUT_DIR = 'tmp/probe-kit'
/** `<repo>/tools/probe` → `<repo>`. Defaults are REPO-relative, not cwd-relative. */
export const REPO_ROOT = resolve(import.meta.dir, '..', '..')

/**
 * Resolve a tool path. Defaults are repo-relative so the very likely operator
 * flow — a pack that landed in the download folder, running the tool by absolute
 * path from there — still finds the manifest; a cwd-local file still wins for an
 * explicit override.
 */
export function toolPath(p: string, cwd = process.cwd()): string {
  if (isAbsolute(p)) return p
  const fromRepo = resolve(REPO_ROOT, p)
  return existsSync(fromRepo) ? fromRepo : resolve(cwd, p)
}
/** The calibration game: a protocol that cannot pass it must not be interpreted. */
const CALIBRATION_GAME = 0

// ================================================================
// Models
// ================================================================

/** One row of the verdict table — one manifest game. */
export interface KitRow {
  game: number
  stage: number
  seed: number
  tag: string
  /** Annotation header outcome (`clear` / `lose`), null when not captured. */
  outcome: string | null
  kills: number | null
  endTick: number | null
  endReason: string | null
  band: ProbeBand | null
  reason: string
  attempts: number | null
  /** This band's contribution to the seed verdict (null = `retry` / unjudged). */
  contribution: SeedVerdict | null
}

export interface KitFinding {
  level: 'error' | 'warn'
  message: string
}

export interface KitReport {
  course: string
  /** True/False when a pack carried a session; null in prep mode. */
  courseShaOk: boolean | null
  packCourseSha: string | null
  startedAt: string | null
  rows: KitRow[]
  findings: KitFinding[]
  /** §0 aggregation over every judged band: 可动 / 不可解 / 未知. */
  seedVerdict: SeedVerdict
  /** game 0 gate: judged `solvable` = pass, judged otherwise = fail, unjudged = unknown. */
  calibration: 'pass' | 'fail' | 'unknown'
  captured: number[]
  judged: number[]
}

// ================================================================
// Core — inspect a delivered pack
// ================================================================

interface SessionJson {
  course?: unknown
  courseSha?: unknown
  startedAt?: unknown
  games?: unknown
}

function readSession(entries: readonly ZipEntry[]): SessionJson {
  const entry = entries.find((e) => e.name === 'session.json')
  if (!entry) throw new Error('kit: pack 里没有 session.json —— 这不是探针会话包')
  return JSON.parse(new TextDecoder().decode(entry.data)) as SessionJson
}

/** Annotation header for one game (the `type:'header'` line), or null. */
function headerOf(lines: readonly AnnotationLine[], game: number): AnnotationLine | null {
  for (const l of lines) {
    if (l.type === 'header' && l.game === game) return l
  }
  return null
}

/**
 * Read a delivered pack against a manifest: annotate it, join the verdicts, and
 * report every inconsistency that would corrupt the reading. Throws on a pack
 * that is not a probe session pack.
 */
export function inspectPack(zipBytes: Uint8Array, manifest: ProbeManifest): KitReport {
  const entries = readStoreZip(zipBytes)
  const session = readSession(entries)
  const findings: KitFinding[] = []

  // ---- session identity ----
  const packCourse = typeof session.course === 'string' ? session.course : null
  if (packCourse !== manifest.course) {
    findings.push({
      level: 'error',
      message: `包 course=${JSON.stringify(packCourse)} 与清单 course=${JSON.stringify(manifest.course)} 不符`,
    })
  }
  const packCourseSha = typeof session.courseSha === 'string' ? session.courseSha : null
  const courseShaOk = packCourseSha !== null && packCourseSha === manifest.courseSha
  if (!courseShaOk) {
    // The browser cannot verify this (the level file is not served) — offline we
    // can, and a mismatch means the run happened against a different build.
    findings.push({
      level: 'error',
      message:
        `courseSha 不符：包=${JSON.stringify(packCourseSha)} 本清单=${manifest.courseSha} ` +
        '—— 操作员判读的是另一个构建，别把它并进本次结论',
    })
  }
  const startedAt = typeof session.startedAt === 'string' ? session.startedAt : null

  // ---- the pack's own (game, stage, seed) table must match the manifest ----
  const sessionGames = Array.isArray(session.games) ? (session.games as unknown[]) : []
  const sessionByGame = new Map<number, { stage: number; seed: number }>()
  for (const g of sessionGames) {
    if (typeof g !== 'object' || g === null) continue
    const rec = g as Record<string, unknown>
    if (typeof rec.game !== 'number') continue
    sessionByGame.set(rec.game, { stage: Number(rec.stage), seed: Number(rec.seed) })
  }
  for (const game of manifest.games) {
    const s = sessionByGame.get(game.game)
    if (!s) continue
    if (s.stage !== game.stage || s.seed !== game.seed) {
      findings.push({
        level: 'error',
        message: `game ${game.game}: 会话表 ${s.stage}/${s.seed} 与清单 ${game.stage}/${game.seed} 不符`,
      })
    }
  }
  // One aggregate line — a per-game list would flood a partial/practice session
  // with seven near-identical lines while saying nothing extra.
  const notInSession = manifest.games.filter((g) => !sessionByGame.has(g.game)).map((g) => g.game)
  if (notInSession.length > 0) {
    findings.push({
      level: 'warn',
      message:
        `未采集 ${notInSession.length} 局：${notInSession.map((g) => `game ${g}`).join(', ')}` +
        '（§T7 要求 7 局全采集；部分会话只能给出它自己的读数）',
    })
  }
  for (const game of sessionByGame.keys()) {
    if (!manifest.games.some((g) => g.game === game)) {
      findings.push({ level: 'warn', message: `会话表里的 game ${game} 不在清单里` })
    }
  }

  // ---- verdicts (the protocol's own validation is authoritative) ----
  const verdictEntry = entries.find((e) => e.name === 'verdicts.jsonl')
  const verdicts = new Map<number, ProbeVerdict>()
  if (verdictEntry) {
    const text = new TextDecoder().decode(verdictEntry.data)
    for (const line of text.split('\n')) {
      if (line.trim() === '') continue
      let parsed: ProbeVerdict
      try {
        parsed = JSON.parse(line) as ProbeVerdict
      } catch {
        findings.push({
          level: 'error',
          message: `verdicts.jsonl 有一行不是 JSON: ${line.slice(0, 80)}`,
        })
        continue
      }
      try {
        validateProbeVerdict(parsed)
      } catch (err) {
        const msg = err instanceof ProbeVerdictError ? err.message : String(err)
        findings.push({ level: 'error', message: `verdicts.jsonl game ${parsed.game}: ${msg}` })
        continue
      }
      if (verdicts.has(parsed.game)) {
        findings.push({ level: 'warn', message: `game ${parsed.game}: 有多条 verdict，取最后一条` })
      }
      verdicts.set(parsed.game, parsed)
    }
  } else {
    findings.push({ level: 'warn', message: 'pack 里没有 verdicts.jsonl（没有任何判读）' })
  }

  // ---- annotation (shared with tools/probe/annotate.ts) ----
  const annotation = annotatePack(zipBytes, manifest)
  const captured = entries
    .map((e) => /^(\d+)\.replay$/.exec(e.name))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => Number(m[1]))
    .sort((a, b) => a - b)

  // ---- rows ----
  const rows: KitRow[] = manifest.games.map((g) => {
    const header = headerOf(annotation.lines, g.game)
    const verdict = verdicts.get(g.game) ?? null
    const band = verdict ? verdict.best.band : null
    return {
      game: g.game,
      stage: g.stage,
      seed: g.seed,
      tag: g.tag,
      outcome: header ? String(header.outcome) : null,
      kills: header ? Number(header.kills) : null,
      endTick: header ? Number(header.endTick) : null,
      endReason: header ? String(header.endReason) : null,
      band,
      reason: verdict ? verdict.best.reason : '',
      attempts: verdict ? verdict.attempts : null,
      contribution: band ? verdictOfBand(band) : null,
    }
  })

  // ---- cross-checks the operator would otherwise have to eyeball ----
  for (const row of rows) {
    if (row.outcome !== null && row.band === null) {
      findings.push({
        level: 'warn',
        message: `game ${row.game}: 已采集（${row.outcome}）但未判读 —— 表格里留空`,
      })
    }
    if (row.outcome === null && row.band !== null) {
      findings.push({ level: 'warn', message: `game ${row.game}: 有 verdict 但没有录像` })
    }
    if (row.outcome !== null && row.band !== null) {
      const claimedClear = row.band === 'solvable' || row.band === 'slight'
      const actuallyCleared = row.outcome === 'clear'
      if (claimedClear !== actuallyCleared) {
        findings.push({
          level: 'warn',
          message:
            `game ${row.game}: band=${row.band}（${claimedClear ? '声称通关' : '声称未通关'}）` +
            ` 与录像结局 ${row.outcome} 不符 —— 二选一是笔误，核对后再解读`,
        })
      }
    }
  }

  const bands = rows.map((r) => r.band).filter((b): b is ProbeBand => b !== null)
  const seedVerdict = aggregateSeedVerdict(bands)
  const calibrationRow = rows.find((r) => r.game === CALIBRATION_GAME) ?? null
  const calibration: KitReport['calibration'] =
    calibrationRow?.band === null || calibrationRow === null
      ? 'unknown'
      : calibrationRow.contribution === 'solvable'
        ? 'pass'
        : 'fail'
  if (calibration === 'fail') {
    findings.push({
      level: 'error',
      message:
        `校准局 game ${CALIBRATION_GAME} 未通过（${calibrationRow?.band}）——` +
        ' 按 §0 协议本次解读整体作废，不要拿其余局的结论去改训练侧',
    })
  }

  return {
    course: manifest.course,
    courseShaOk: packCourseSha === null ? null : courseShaOk,
    packCourseSha,
    startedAt,
    rows,
    findings,
    seedVerdict,
    calibration,
    captured,
    judged: [...verdicts.keys()].sort((a, b) => a - b),
  }
}

/** Prep-mode report: the manifest's games, nothing captured, nothing judged. */
export function emptyReport(manifest: ProbeManifest): KitReport {
  return {
    course: manifest.course,
    courseShaOk: null,
    packCourseSha: null,
    startedAt: null,
    rows: manifest.games.map((g) => ({
      game: g.game,
      stage: g.stage,
      seed: g.seed,
      tag: g.tag,
      outcome: null,
      kills: null,
      endTick: null,
      endReason: null,
      band: null,
      reason: '',
      attempts: null,
      contribution: null,
    })),
    findings: [],
    seedVerdict: 'unknown',
    calibration: 'unknown',
    captured: [],
    judged: [],
  }
}

// ================================================================
// Rendering
// ================================================================

const BAND_LABEL: Record<ProbeBand, string> = {
  solvable: '可解',
  slight: '略难',
  tough: '难',
  unsolvable: '无解',
  retry: '再试一次',
}

/** §0 aggregation targets (a band's `seedVerdict`, and the per-row contribution). */
const SEED_LABEL: Record<SeedVerdict, string> = {
  solvable: '可动',
  unsolvable: '不可解',
  unknown: '未知',
}

/** The conclusion line for each §0 outcome. */
const SEED_VERDICT_LINE: Record<SeedVerdict, string> = {
  solvable: '开局可动',
  unsolvable: '人类判不可解（可被任一后续通关推翻）',
  unknown: '仍未知（未读出「无路」；死亡本身不构成信息）',
}

/** The seven launch links, built with the same helper the in-game launcher uses. */
export function gameLinks(manifest: ProbeManifest, base: string): string[] {
  const root = `${base.replace(/\/+$/, '')}/`
  return manifest.games.map((g) => `${root}${probeGameHref(manifest.course, g.game)}`)
}

const dash = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : String(v))

/**
 * The operator sheet: links + the verdict table + the band legend + what the pack
 * review found. Deterministic — nothing here reads the wall clock (the only
 * timestamp is the session's own `startedAt`), so it is unit-tested byte-for-byte.
 */
export function renderSheet(report: KitReport, manifest: ProbeManifest, base: string): string {
  const out: string[] = []
  out.push(`# 开局可学性探针 —— 操作员表（${report.course}）`)
  out.push('')
  out.push('协议：人类熟练玩家是关卡难度的唯一尺子 —— 每局的 band 就是结论，按一张表聚合。')
  out.push('1. verdict 的 band 由人类判读，不校验把数；`无解` 必须带理由（缺理由拒打包）。')
  out.push(
    '2. 判读口径：任一局「可解/略难」⇒ **可动**；否则任一局「无解+理由」⇒ **不可解**；' +
      '其余（难/没判）⇒ **未知**（`再试一次` 不聚合）。',
  )
  out.push(
    '3. 「不可解」是**判读不是证明**，天然可推翻：同一局后来的结论覆盖先前的，' +
      '任何人后来打通了就当场划掉。',
  )
  out.push(
    `4. **校准局 game ${CALIBRATION_GAME} 不通过 ⇒ 本次解读整体作废**，不要拿其余局去改训练侧。`,
  )
  out.push('')

  out.push(`## 启动链接（dev server ${base}）`)
  out.push('')
  for (const [i, link] of gameLinks(manifest, base).entries()) {
    const g = manifest.games[i]
    out.push(`- game ${g.game}（stage ${g.stage} / seed ${g.seed}，${g.tag}）：${link}`)
  }
  out.push('')

  out.push('## verdict 表')
  out.push('')
  out.push(
    '| game | stage/seed | tag | 结果 | kills | 死亡tick | band | 理由 | attempts | seed 判定 |',
  )
  out.push('|---|---|---|---|---|---|---|---|---|---|')
  for (const r of report.rows) {
    out.push(
      `| ${r.game} | ${r.stage}/${r.seed} | ${r.tag} | ${dash(r.outcome)} | ${dash(r.kills)} | ` +
        `${dash(r.endTick)} | ${r.band ? BAND_LABEL[r.band] : '—'} | ${dash(r.reason)} | ` +
        `${dash(r.attempts)} | ${r.contribution ? SEED_LABEL[r.contribution] : '—'} |`,
    )
  }
  out.push('')

  out.push('## band 口径（`src/probe/verdict.ts` 单一来源）')
  out.push('')
  out.push('| band | kills | 含义 | 聚合 |')
  out.push('|---|---|---|---|')
  for (const spec of BAND_TABLE) {
    out.push(
      `| ${BAND_LABEL[spec.band]} (\`${spec.band}\`) | ${spec.kills} | ${spec.meaning} | ` +
        `${spec.seedVerdict ? SEED_LABEL[spec.seedVerdict] : '不聚合'} |`,
    )
  }
  out.push('')

  // A report came from a pack once it carries session identity or a recording.
  const delivered = report.packCourseSha !== null || report.captured.length > 0
  const judged = report.judged.length > 0

  if (!delivered) {
    out.push('## 交付')
    out.push('')
    out.push(
      '1. 7 局全部跑完：每局点一个 band（`无解` 必须填理由），打完点「结束会话」下载会话包。',
    )
    out.push(
      '2. 收官一条命令：`bun tools/probe/kit.ts <下载的 pack.zip>` —— 输出 `annotation.jsonl` + 填好的本表。',
    )
    out.push('3. 交付物 = 会话包 + 本表 + annotation.jsonl。校准局不通过则本轮整体作废。')
    out.push('')
    out.push('## 校验')
    out.push('')
    out.push('- 尚未开始：这是**跑之前的空表**（行由清单派生，不会与浏览器实际跑的局错位）')
    out.push('')
    return out.join('\n')
  }

  out.push('## 校验')
  out.push('')
  if (report.courseShaOk !== null) {
    out.push(
      `- courseSha：${report.courseShaOk ? '与清单一致 ✅' : `不一致 ❌（包=${dash(report.packCourseSha)}）`}`,
    )
  }
  out.push(
    `- 已采集 ${report.captured.length} 局（${report.captured.map((g) => `game ${g}`).join(', ') || '无'}）；` +
      `已判读 ${report.judged.length} 局（${report.judged.map((g) => `game ${g}`).join(', ') || '无'}）`,
  )
  if (report.findings.length === 0) {
    out.push('- 无异常')
  } else {
    for (const f of report.findings) {
      out.push(`- ${f.level === 'error' ? '❌ 错误' : '⚠️ 提示'}：${f.message}`)
    }
  }
  out.push('')

  if (judged) {
    out.push('## 结论')
    out.push('')
    out.push(
      `- 校准局：${
        report.calibration === 'pass'
          ? '通过 ✅'
          : report.calibration === 'fail'
            ? '未通过 ❌（本次解读作废）'
            : '未判读'
      }`,
    )
    out.push(`- seed 聚合（§0 表）：**${SEED_VERDICT_LINE[report.seedVerdict]}**`)
    out.push('')
    out.push(
      '> 「不可解」是操作员读出「无路」的**判读**，不是穷举证明（60 帧动作空间搜不完，' +
        '也不许拿 God AI/搜索当裁判）。它可推翻：任一后续通关直接划掉。',
    )
    out.push('')
  }

  return out.join('\n')
}

// ================================================================
// CLI
// ================================================================

export interface KitArgs {
  pack: string | null
  manifestPath: string
  base: string
  outDir: string
}

/** `--flag value` pairs, plus the first bare token as the pack path. */
export function parseArgs(argv: readonly string[]): KitArgs {
  let manifestPath = DEFAULT_MANIFEST
  let base = DEFAULT_BASE
  let outDir = DEFAULT_OUT_DIR
  let pack: string | null = null
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--manifest') manifestPath = argv[++i] ?? DEFAULT_MANIFEST
    else if (a === '--base') base = argv[++i] ?? DEFAULT_BASE
    else if (a === '--out-dir') outDir = argv[++i] ?? DEFAULT_OUT_DIR
    else if (!a.startsWith('--')) pack = pack ?? a
  }
  return { pack, manifestPath, base, outDir }
}

/**
 * Resolve the pack the operator typed: an explicit path is taken as given, while
 * a bare file name is looked up in cwd and then the OS download folder — which
 * is where a browser download actually lands.
 */
export function resolvePackPath(
  spec: string,
  cwd = process.cwd(),
  home = homedir(),
  repoRoot = REPO_ROOT,
): string | null {
  const explicit = spec.includes('/') || spec.includes('\\')
  const candidates = explicit
    ? [resolve(cwd, spec)]
    : [resolve(cwd, spec), resolve(repoRoot, spec), join(home, 'Downloads', spec)]
  for (const c of candidates) {
    if (existsSync(c)) return c
  }
  return null
}

export function run(argv: readonly string[] = process.argv.slice(2)): number {
  const args = parseArgs(argv)
  const manifestPath = toolPath(args.manifestPath)
  const outDir = isAbsolute(args.outDir) ? args.outDir : resolve(REPO_ROOT, args.outDir)
  const manifest = parseProbeManifestText(readFileSync(manifestPath, 'utf8'))

  if (!args.pack) {
    // ---- prep: what the human needs BEFORE running anything ----
    const sheet = renderSheet(emptyReport(manifest), manifest, args.base)
    process.stdout.write(sheet + '\n')
    const outPath = join(outDir, 'operator-sheet.md')
    mkdirSync(dirname(outPath), { recursive: true })
    writeFileSync(outPath, sheet + '\n')
    console.log(`\nkit: 空表已写入 ${outPath}（把它交给操作员；开跑用上面 7 条链接）`)
    return 0
  }

  const packPath = resolvePackPath(args.pack)
  if (!packPath) {
    console.error(`kit: 找不到会话包 ${args.pack}`)
    console.error('  （已在 cwd 与 ~/Downloads 找过同名文件；也可以直接给完整路径）')
    return 1
  }

  const packBytes = readFileSync(packPath)
  const report = inspectPack(packBytes, manifest)
  const sheet = renderSheet(report, manifest, args.base)
  mkdirSync(outDir, { recursive: true })
  const annPath = join(outDir, 'annotation.jsonl')
  const sheetPath = join(outDir, 'verdicts-table.md')

  // Annotation text is the shared `annotatePack` result, serialized identically
  // to `tools/probe/annotate.ts` (one JSON object per line).
  const annotation = annotatePack(packBytes, manifest)
  writeFileSync(annPath, annotation.lines.map((l) => JSON.stringify(l)).join('\n') + '\n')
  writeFileSync(sheetPath, sheet + '\n')

  console.log(`kit: 包 ${packPath}`)
  console.log(`kit: ${annotation.games} 局 → ${annPath}`)
  console.log(`kit: verdict 表 → ${sheetPath}`)
  for (const f of report.findings) {
    console.log(`  ${f.level === 'error' ? '❌' : '⚠️'} ${f.message}`)
  }
  console.log(
    `kit: 已采集 ${report.captured.length} / 已判读 ${report.judged.length} / ` +
      `校准局 ${report.calibration} / 结论 ${report.seedVerdict}`,
  )
  return report.findings.some((f) => f.level === 'error') ? 1 : 0
}

if (import.meta.main) process.exit(run())
