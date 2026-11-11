/**
 * notebook-cmd.test.ts — 下行命令通道（`stopOthers` → 其它页停会话）的行为契约。
 *
 * 要点：只停**其它**活会话、发起者自己与陈旧页除外；同目标 TTL 内不重复派；命令过期不下发；
 * ack 走 op 日志那一行（`cmd:stop-session id=… ok|fail`），ack 后不再下发。
 */
import { afterAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import {
  ackNotebookCmd,
  CMD_TTL_MS,
  enqueueStopOthers,
  liveNotebookSessions,
  notebookCmdsPath,
  parseAckDetail,
  parseStopOthers,
  pendingNotebookCmds,
  putNotebookCmd,
  readNotebookCmds,
  isPpoWorkerSession,
  STOP_OTHERS_MAX_TARGETS,
  stopOthersEvidence,
} from '../src/server/api/notebook-cmd'
import { CONSOLE_ROUTES } from '../src/server/route-table'

const T0 = 1_790_000_000_000
const temps: string[] = []
const tempDir = (): string => {
  const d = mkdtempSync(path.join(tmpdir(), 'bncc-'))
  temps.push(d)
  return d
}
afterAll(() => {
  for (const d of temps) rmSync(d, { recursive: true, force: true })
})

/** 造一个会话（ops 判「活」，可选 `.log` 判「是不是 PPO worker 页」）。 */
function seedSession(
  dir: string,
  site: string,
  session: string,
  mtimeMs: number,
  opts: { worker?: boolean; logText?: string } = {},
): void {
  const siteDir = path.join(dir, site)
  mkdirSync(siteDir, { recursive: true })
  const file = path.join(siteDir, `${session}.ops.jsonl`)
  writeFileSync(file, '{"v":1}\n', 'utf8')
  const t = new Date(mtimeMs)
  // utimes 用秒，毫秒精度对本用例够用（窗口是分钟级）
  require('node:fs').utimesSync(file, t, t)
  // 默认是 PPO worker 页（`[worker]` 前缀 = worker.py/bc_job/download 的真实输出形状）；
  // `worker:false` = 只携运日志的普通页/离线页（没有那一行），不该被停。
  if (opts.worker !== false || opts.logText !== undefined) {
    writeFileSync(
      path.join(siteDir, `${session}.log`),
      opts.logText ?? '[12:00:01] [worker] job abc claimed [mode=ok]\n',
      'utf8',
    )
  }
}

describe('parseStopOthers', () => {
  it('只认 cell-start 且 stopOthers=1', () => {
    expect(parseStopOthers({ op: 'offline:cell-start', detail: 'cell=0 stopOthers=1' })).toBe(true)
    expect(parseStopOthers({ op: 'offline:cell-start', detail: 'cell=0 stopOthers=0' })).toBe(false)
    expect(parseStopOthers({ op: 'offline:cell-start', detail: 'cell=0' })).toBe(false)
    expect(parseStopOthers({ op: 'offline:detected', detail: 'stopOthers=1' })).toBe(false)
    // 不被 stopOthers=10 / stopOthers=11 之类的长数字骗过
    expect(parseStopOthers({ op: 'offline:cell-start', detail: 'stopOthers=10' })).toBe(false)
  })
})

describe('enqueueStopOthers', () => {
  it('只给其它活会话派；发起者、陈旧页、备份段都不算', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    seedSession(dir, 'kaggle', 'a', T0 - 60_000)
    seedSession(dir, 'colab', 'b', T0 - 5 * 60_000)
    seedSession(dir, 'kaggle', 'stale', T0 - 20 * 60_000)
    seedSession(dir, 'kaggle', 'x.1', T0) // 轮转备份段：文件名是 `<session>.1.ops.jsonl`
    seedSession(dir, 'aistudio', 'gone', T0 - 11 * 60_000)

    const r = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    const got = r.created.map((c) => `${c.site}:${c.session}`).sort()
    expect(got).toEqual(['colab:b', 'kaggle:a'])
    expect(r.created.every((c) => c.action === 'stop-session')).toBe(true)
    expect(r.created.every((c) => c.by === 'kaggle:self' && c.status === 'pending')).toBe(true)
    expect(new Set(r.created.map((c) => c.id)).size).toBe(2)
  })

  it('幂等：同一目标在 TTL 内已有 pending 就不再派', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    seedSession(dir, 'kaggle', 'a', T0)
    const first = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    expect(first.created.length).toBe(1)
    const again = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0 + 60_000)
    expect(again.created.length).toBe(0)
    expect(again.skippedPending).toBe(1)
    // TTL 过了才允许再来一条（同时把目标页的 ops 写新：它得仍然算「活」，否则是被活窗口挡掉的）
    const later0 = T0 + CMD_TTL_MS + 1
    seedSession(dir, 'kaggle', 'a', later0)
    const later = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, later0)
    expect(later.created.length).toBe(1)
    expect(readNotebookCmds(dir, 'kaggle', 'a').length).toBe(2)
  })

  it('单次最多派 STOP_OTHERS_MAX_TARGETS 个（按最新活跃优先）', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    for (let i = 0; i < STOP_OTHERS_MAX_TARGETS + 2; i += 1) {
      seedSession(dir, 'kaggle', `p${i}`, T0 - i * 1000)
    }
    const r = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    expect(r.created.length).toBe(STOP_OTHERS_MAX_TARGETS)
    expect(r.created.map((c) => c.session)).toEqual(
      Array.from({ length: STOP_OTHERS_MAX_TARGETS }, (_, i) => `p${i}`),
    )
  })

  it('只停 PPO worker 页：只搬日志的普通页 / 离线页不算目标', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    seedSession(dir, 'kaggle', 'worker', T0) // 默认带 `[worker]`
    seedSession(dir, 'kaggle', 'plain', T0, { worker: false })
    seedSession(dir, 'kaggle', 'offline', T0, {
      logText: '[12:00:00] [offline] 接管课程 xxx；开始本地训练\n',
    })
    const r = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    expect(r.created.map((c) => c.session)).toEqual(['worker'])
    expect(r.candidates).toBe(3)
    expect(r.skippedNotWorker).toBe(2)
    expect(stopOthersEvidence(r)).toContain('skipped_non_worker=2')
    // 排障口：requirePpoWorker:false 才看全部活会话（上面那条 worker 命令还在 TTL 内 ⇒ 跳过它）
    const all = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0 + 60_000, {
      requirePpoWorker: false,
    })
    expect(all.candidates).toBe(3)
    expect(all.skippedPending).toBe(1)
    expect(all.created.map((c) => c.session).sort()).toEqual(['offline', 'plain'])
  })

  it('一个别的页都没有 ⇒ 一条不派（也不炸）', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    const r = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    expect(r.created).toEqual([])
    expect(r.candidates).toBe(0)
    expect(stopOthersEvidence(r)).toContain('targets=0/0')
  })
})

describe('取件 / ack', () => {
  it('pending 且 TTL 内才下发；过期的停在文件里但不发', () => {
    const dir = tempDir()
    const base = {
      v: 1 as const,
      site: 'kaggle' as const,
      session: 'a',
      action: 'stop-session' as const,
      by: 'kaggle:self',
    }
    putNotebookCmd(dir, { ...base, id: 'cmd-fresh', at: T0, status: 'pending' })
    putNotebookCmd(dir, { ...base, id: 'cmd-old', at: T0 - CMD_TTL_MS - 1, status: 'pending' })
    putNotebookCmd(dir, { ...base, id: 'cmd-done', at: T0, status: 'ok' })

    const got = pendingNotebookCmds(dir, 'kaggle', 'a', T0).map((c) => c.id)
    expect(got).toEqual(['cmd-fresh'])
    expect(readNotebookCmds(dir, 'kaggle', 'a').length).toBe(3)
  })

  it('ack 后不再下发，状态与原因落盘；未知 id 不造记录', () => {
    const dir = tempDir()
    const cmd = putNotebookCmd(dir, {
      v: 1,
      id: 'cmd-1',
      site: 'colab',
      session: 'b',
      action: 'stop-session',
      at: T0,
      by: 'kaggle:self',
      status: 'pending',
    })
    expect(cmd.id).toBe('cmd-1')
    expect(ackNotebookCmd(dir, 'colab', 'b', 'cmd-1', 'ok')).toBe(true)
    expect(pendingNotebookCmds(dir, 'colab', 'b', T0)).toEqual([])
    expect(readNotebookCmds(dir, 'colab', 'b')[0]?.status).toBe('ok')

    // 重试 ack（页面重发同一行）：幂等，按最后一次为准
    expect(ackNotebookCmd(dir, 'colab', 'b', 'cmd-1', 'fail', '停止按钮没找到')).toBe(true)
    const rows = readNotebookCmds(dir, 'colab', 'b')
    expect(rows.length).toBe(1)
    expect(rows[0]?.status).toBe('fail')
    expect(rows[0]?.reason).toBe('停止按钮没找到')

    expect(ackNotebookCmd(dir, 'colab', 'b', 'cmd-nope', 'ok')).toBe(false)
    expect(readNotebookCmds(dir, 'colab', 'b').length).toBe(1)
  })

  it('parseAckDetail 只认 id=… ok|fail 开头', () => {
    expect(parseAckDetail('id=cmd-abc ok')).toEqual({ id: 'cmd-abc', status: 'ok' })
    expect(parseAckDetail('id=cmd-abc fail 按钮没找到')).toEqual({
      id: 'cmd-abc',
      status: 'fail',
    })
    expect(parseAckDetail('ok')).toBeNull()
    expect(parseAckDetail('id= ok')).toBeNull()
    expect(parseAckDetail('')).toBeNull()
  })
})

describe('isPpoWorkerSession', () => {
  it('日志尾巴有 [worker] / [worker-supervisor] 才算', () => {
    const dir = tempDir()
    seedSession(dir, 'colab', 'a', T0)
    seedSession(dir, 'colab', 'sup', T0, {
      logText: '[12:00:00] [worker-supervisor] worker exited\n',
    })
    seedSession(dir, 'colab', 'plain', T0, { logText: '只是搬运的 cell 输出\n' })
    expect(isPpoWorkerSession(dir, 'colab', 'a')).toBe(true)
    expect(isPpoWorkerSession(dir, 'colab', 'sup')).toBe(true)
    expect(isPpoWorkerSession(dir, 'colab', 'plain')).toBe(false)
    // 日志缺席（只报过 op 的页）⇒ fail closed：不停
    expect(isPpoWorkerSession(dir, 'colab', 'missing')).toBe(false)
  })

  it('只看尾巴：标记老在 64KB 之前 + 新内容是别的 ⇒ 不算 worker 页', () => {
    const dir = tempDir()
    const old = '[12:00:00] [worker] job old claimed\n'
    const filler = `${'x'.repeat(200)}\n`.repeat(500) // ~100KB 的后续输出，把标记挤出尾巴
    seedSession(dir, 'colab', 'gone', T0, { logText: old + filler })
    expect(isPpoWorkerSession(dir, 'colab', 'gone')).toBe(false)
    // 尾巴窗口足够大时又命中（参数化入口，不写死常量）
    expect(isPpoWorkerSession(dir, 'colab', 'gone', { tailBytes: 200 * 1024 })).toBe(true)
  })
})

describe('路径与读数', () => {
  it('命令文件与 ops 同目录；非法会话/越界抛', () => {
    const dir = tempDir()
    expect(notebookCmdsPath(dir, 'kaggle', 'session-1')).toBe(
      path.join(path.resolve(dir), 'kaggle', 'session-1.cmds.json'),
    )
    expect(() => notebookCmdsPath(dir, 'kaggle', 'a/../b')).toThrow()
    expect(() => notebookCmdsPath(dir, 'nope' as 'kaggle', 'a')).toThrow()
  })

  it('liveNotebookSessions 按 mtime 降序、跳过备份段与非法站点目录', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'old', T0 - 5 * 60_000)
    seedSession(dir, 'kaggle', 'new', T0 - 1000)
    seedSession(dir, 'kaggle', 'x.1', T0)
    mkdirSync(path.join(dir, 'notes'), { recursive: true })
    const rows = liveNotebookSessions(dir, T0).map((r) => r.session)
    expect(rows).toEqual(['new', 'old'])
  })

  it('证据行带目标清单与跳过计数', () => {
    const dir = tempDir()
    seedSession(dir, 'kaggle', 'self', T0)
    seedSession(dir, 'kaggle', 'a', T0)
    const r = enqueueStopOthers(dir, { site: 'kaggle', session: 'self' }, T0)
    const line = stopOthersEvidence(r)
    expect(line).toContain('targets=1/1')
    expect(line).toContain('kaggle:a')
  })

  it('取件口登记在路由表里（GET，且排在通配之前）', () => {
    const idx = CONSOLE_ROUTES.findIndex((r) => r.path === '/api/notebookCmds')
    expect(idx).toBeGreaterThanOrEqual(0)
    expect(CONSOLE_ROUTES[idx]?.methods).toEqual(['GET'])
    const wildcard = CONSOLE_ROUTES.findIndex((r) => r.path === '/api/log/*')
    expect(idx).toBeLessThan(wildcard)
  })
})
