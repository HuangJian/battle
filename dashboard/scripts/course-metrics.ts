#!/usr/bin/env bun
/** course-metrics.ts — 课程指标备份库 CLI（plan/course-metrics-persistence §3.6）。
 *
 *  它不是训练操作入口（AGENTS §5 的「唯一入口 = 控制台」不适用）：只读 `tmp/`、只写备份库。
 *  `sync` 是 `tmp-clean --apply` 转存段调用的同一个实现（游标/锚点/复位只有一份代码）。
 */

import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT, tmpLogsDir } from '../src/core/paths'
import {
  LockBusyError,
  archivePathFor,
  coldArchiveBytes,
  coldCourses,
  compactAll,
  libraryRoot,
  packDays,
  readArchiveIndex,
  readArchiveText,
  statusRows,
  syncAll,
  tarBin,
  verifyCold,
  verifyHot,
} from '../src/server/course-metrics'

function flags(argv: string[]): Record<string, string | true> {
  const out: Record<string, string | true> = {}
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!
    if (!a.startsWith('--')) continue
    const key = a.slice(2)
    const next = argv[i + 1]
    if (next == null || next.startsWith('--')) out[key] = true
    else {
      out[key] = next
      i++
    }
  }
  return out
}

function str(v: string | true | undefined): string {
  return typeof v === 'string' ? v : ''
}

function num(v: string | true | undefined, fallback: number): number {
  const n = Number(str(v))
  return Number.isFinite(n) && str(v) !== '' ? n : fallback
}

const KB = 1024

async function withLockRetry<T>(fn: () => T | Promise<T>, waitMs = 30_000): Promise<T> {
  const t0 = Date.now()
  for (;;) {
    try {
      return await fn()
    } catch (e) {
      if (!(e instanceof LockBusyError)) throw e
      if (Date.now() - t0 > waitMs) throw e
      await Bun.sleep(500)
    }
  }
}

async function cmdSync(f: Record<string, string | true>, quiet: boolean): Promise<number> {
  const course = str(f.course) || '*'
  const budget = num(f.budget, 0)
  const r = await withLockRetry(() => syncAll({ course, budgetBytes: budget }))
  if (!quiet) {
    console.log(
      `指标转存: 同步 ${r.courses} 课 / 新增 ${(r.bytes / KB).toFixed(1)} KB（${r.lines} 行）` +
        ` ｜ 跳过 ${r.skipped} ｜ 复位 ${r.resets.length} ｜ 失败 ${r.failed.length}` +
        `${r.budgetHit ? ' ｜ 预算用尽' : ''}`,
    )
    for (const x of r.resets) console.log(`  复位 ${x.course}/${x.rel}: ${x.reason}`)
    for (const x of r.failed) console.log(`  失败 ${x.course}/${x.rel}: ${x.error}`)
  }
  return r.failed.length > 0 ? 1 : 0
}

function cmdStatus(): number {
  const rows = statusRows()
  const cold = coldCourses()
  if (rows.length === 0 && cold.length === 0) console.log('库内还没有备份（先跑 sync）')
  for (const r of rows) {
    const lag = r.files.reduce((s, x) => s + x.lagBytes, 0)
    const last = r.lastSourceActivity > 0 ? new Date(r.lastSourceActivity).toISOString() : '—'
    console.log(
      `[hot] ${r.course}  落后 ${lag}B ｜ 最后源活动 ${last}` +
        ` ｜ 打包倒计时 ${r.packInDays == null ? '—' : `${r.packInDays}d`}`,
    )
  }
  for (const course of cold) {
    const idx = readArchiveIndex(course)
    console.log(
      `[cold] ${course}  包 ${((coldArchiveBytes(course) ?? 0) / 1024 / 1024).toFixed(1)}MiB` +
        ` ｜ 成员 ${idx?.members.length ?? 0}`,
    )
  }
  return 0
}

async function cmdVerify(): Promise<number> {
  const hot = verifyHot()
  const cold = await verifyCold()
  const all = [...hot, ...cold]
  if (all.length === 0) {
    console.log('对账通过：热备份与冷存包逐项一致')
    return 0
  }
  for (const x of all) console.log(`不一致 ${x.course}/${x.rel}: ${x.reason}`)
  console.log(`对账失败：${all.length} 项`)
  return 1
}

async function cmdCompact(f: Record<string, string | true>): Promise<number> {
  const r = await compactAll({
    course: str(f.course) || '*',
    days: num(f.days, packDays()),
    max: f.max === undefined ? undefined : num(f.max, 1),
  })
  for (const p of r.packed) {
    console.log(
      `冷存 ${p.course}: ${(p.bytes / 1024 / 1024).toFixed(1)}MiB / ${p.members} 成员` +
        `${p.generation ? `（旧世代 ${p.generation}）` : ''}`,
    )
  }
  for (const x of r.failed) console.log(`冷存失败 ${x.course}: ${x.error}`)
  if (r.packed.length === 0 && r.failed.length === 0)
    console.log('没有到期的课（tmp 仍在或未满阈值）')
  return r.failed.length > 0 ? 1 : 0
}

async function cmdBackup(): Promise<number> {
  const sync = await withLockRetry(() => syncAll({ budgetBytes: 0 }))
  if (sync.failed.length > 0) {
    for (const x of sync.failed) console.log(`转存失败 ${x.course}/${x.rel}: ${x.error}`)
    return 1
  }
  const root = libraryRoot()
  const rel = `backups/course-metrics-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.tar.gz`
  const dest = path.join(root, rel)
  mkdirSync(path.dirname(dest), { recursive: true })
  const proc = Bun.spawn(
    [
      tarBin(),
      '-czf',
      rel,
      '--exclude=./backups',
      '--exclude=./.lock',
      '--exclude=./.lock.stale.*',
      '.',
    ],
    { cwd: root, stdout: 'pipe', stderr: 'pipe', windowsHide: true },
  )
  const [err, code] = await Promise.all([
    new Response(proc.stderr as ReadableStream).text(),
    proc.exited,
  ])
  if (code !== 0) {
    console.log(`备份失败：${err.trim().slice(0, 300)}`)
    return 1
  }
  console.log(`仓库外导出件: ${dest}（${(statSync(dest).size / 1024 / 1024).toFixed(1)}MiB）`)
  console.log('记得把它搬到仓库外（NAS/云盘），路径记当日 .workbuddy/memory/')
  return 0
}

function readHotOrMirror(course: string, file: string): { text: string; from: string } | null {
  for (const base of [path.join(tmpLogsDir(), course), path.join(libraryRoot(), course)]) {
    for (const rel of [file, `traj/${file}`]) {
      const p = path.join(base, rel)
      try {
        if (statSync(p).isFile()) return { text: readFileSync(p, 'utf8'), from: p }
      } catch {
        /* next */
      }
    }
  }
  return null
}

async function cmdCat(f: Record<string, string | true>): Promise<number> {
  const course = str(f.course)
  const file = str(f.file)
  if (!course || !file || file.includes('/') || file.includes('\\')) {
    console.error('用法: cat --course C --file training_log.jsonl')
    return 2
  }
  const hot = readHotOrMirror(course, file)
  if (hot) {
    process.stdout.write(hot.text)
    return 0
  }
  const idx = readArchiveIndex(course)
  const member = `${course}/${file}`
  const memberTraj = `${course}/traj/${file}`
  const rel = idx?.members.includes(member)
    ? file
    : idx?.members.includes(memberTraj)
      ? `traj/${file}`
      : ''
  if (!rel) {
    console.error(`冷存包内没有 ${file}`)
    return 1
  }
  const text = await readArchiveText(course, rel)
  if (text == null) {
    console.error('解压失败（见日志）')
    return 1
  }
  process.stdout.write(text)
  return 0
}

async function cmdRestore(f: Record<string, string | true>): Promise<number> {
  const course = str(f.course)
  if (!course) {
    console.error('用法: restore --course C [--to tmp] [--force]')
    return 2
  }
  const toRaw = str(f.to) || 'tmp'
  const base = path.isAbsolute(toRaw) ? toRaw : path.resolve(REPO_ROOT, toRaw)
  const target = path.join(base, course)
  const force = f.force === true
  if (existsSync(target)) {
    if (!force) {
      console.error(`目标已存在：${target}（--force 才覆盖）`)
      return 2
    }
    // 评审 F11：`--force` 不得把**正在训练**的课抹掉（账本被一份旧快照覆盖 = 制造第二起事故）。
    // 判据用训练侧自己的锁文件（`.run_rl.lock`），不看 registry（CLI 跨进程读不到活体登记）。
    if (existsSync(path.join(target, '.run_rl.lock'))) {
      console.error(`拒绝覆盖活体课：${target} 有 .run_rl.lock（先停课再 restore）`)
      return 2
    }
    rmSync(target, { recursive: true, force: true })
  }
  mkdirSync(base, { recursive: true })
  const hot = path.join(libraryRoot(), course)
  if (existsSync(path.join(hot, 'mirror-meta.json'))) {
    cpSync(hot, target, { recursive: true })
    console.log(`已恢复（热备份）：${target}`)
    return 0
  }
  if (existsSync(archivePathFor(course))) {
    const archiveRel = path.relative(base, archivePathFor(course))
    const proc = Bun.spawn([tarBin(), '-xzf', archiveRel, course], {
      cwd: base,
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
    })
    const [err, code] = await Promise.all([
      new Response(proc.stderr as ReadableStream).text(),
      proc.exited,
    ])
    if (code !== 0) {
      console.error(`解包失败：${err.trim().slice(0, 300)}`)
      return 1
    }
    console.log(`已恢复（冷存包）：${target}`)
    return 0
  }
  console.error(`没有可恢复的备份：${course}`)
  return 1
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  const quiet = argv.includes('--quiet')
  const cmd = argv[0] ?? ''
  const f = flags(argv.slice(1))
  switch (cmd) {
    case 'sync':
      return cmdSync(f, quiet)
    case 'status':
      // memo 占用只在服务端进程里（CLI 跨进程读不到）：见 GET /api/courseMetrics。
      return cmdStatus()
    case 'verify':
      return cmdVerify()
    case 'compact':
      return cmdCompact(f)
    case 'backup':
      return cmdBackup()
    case 'restore':
      return cmdRestore(f)
    case 'cat':
      return cmdCat(f)
    case '':
    case '--help':
      console.log(
        '用法: course-metrics <sync|status|verify|compact|backup|restore|cat> [--course C] [--budget N] [--days D] [--file F] [--to DIR] [--force]',
      )
      return 0
    default:
      console.error(`未知命令: ${cmd}`)
      return 2
  }
}

void main().then(
  (code) => process.exit(code),
  (e) => {
    console.error(e instanceof Error ? e.message : String(e))
    process.exit(e instanceof LockBusyError ? 2 : 1)
  },
)
