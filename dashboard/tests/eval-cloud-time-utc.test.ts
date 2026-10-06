/** eval-cloud-time-utc.test.ts — 云机（`nodes` 含 `cloud`）写的 eval 时间戳是 UTC 钟，
 *  控制台读侧必须转成本机时区再显示（2026-10-06 用户报障：`x21-psh-a` it80 前的时间差 8h）。
 *
 *  分层：src/server/iters.ts（readEvalSummaries / readLatestEvalGames）。
 *
 *  ⚠️ 时区纪律：bun 的 test runner 在**本机是 TZ=UTC**（实测 `getTimezoneOffset() === 0`），
 *  若不动 TZ，UTC→本机 是恒等变换 ⇒ 测试会在地道里空过（先红验证时实测到过）。
 *  故本文件把 TZ 钉死在 Asia/Shanghai（+08:00，与本机生产机一致），期望值写死；
 *  开头有 offset 卫兵：运行时若忽略 TZ 就当场红，而不是悄悄退化成空断言。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readEvalSummaries, readLatestEvalGames } from '../src/server/iters'

const ORIG_TZ = process.env.TZ
process.env.TZ = 'Asia/Shanghai'

const dirs: string[] = []
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
  if (ORIG_TZ === undefined) delete process.env.TZ
  else process.env.TZ = ORIG_TZ
})

function mkDir(name: string): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), `eval-cloud-tz-${name}-`))
  dirs.push(dir)
  return dir
}

const CLOUD_UTC = '2026-10-05 21:39:04'
/** Asia/Shanghai（+08:00）下 CLOUD_UTC 的本机读数。 */
const CLOUD_LOCAL = '2026-10-06 05:39:04'
/** 同轮本地机写的 iteration time（对照：它本来就带本机钟，不许被改）。 */
const LOCAL_STAMP = '2026-10-06 05:39:00'

function writeLog(dir: string, lines: unknown[]): void {
  writeFileSync(
    path.join(dir, 'eval_log.jsonl'),
    lines.map((l) => JSON.stringify(l)).join('\n') + '\n',
  )
}

describe('TZ 卫兵', () => {
  it('测试进程真的跑在 Asia/Shanghai（+08:00），否则下面的期望值没有意义', () => {
    expect(new Date().getTimezoneOffset()).toBe(-480)
  })
})

describe('readEvalSummaries：云机 summary time UTC → 本机时区', () => {
  it('nodes 含 cloud → 转换 +8h（对照组：本机行原样）', () => {
    const dir = mkDir('summary-cloud')
    writeLog(dir, [
      {
        event: 'eval_summary',
        iter: 10,
        wver: 'w',
        time: CLOUD_UTC,
        games: 4,
        nodes: { cloud: 400 },
      },
      {
        event: 'eval_summary',
        iter: 11,
        wver: 'w',
        time: LOCAL_STAMP,
        games: 4,
        nodes: { self: 4 },
      },
    ])
    const out = readEvalSummaries(dir)
    expect(out.get(10)!.time).toBe(CLOUD_LOCAL)
    expect(out.get(11)!.time).toBe(LOCAL_STAMP)
  })

  it('nodes 全是本机节点（无 cloud）→ 原样透传', () => {
    const dir = mkDir('summary-local')
    writeLog(dir, [
      {
        event: 'eval_summary',
        iter: 100,
        wver: 'w',
        time: '2026-10-06 07:57:11',
        nodes: { a97: 37, local: 110, mac: 119, self: 134 },
      },
    ])
    expect(readEvalSummaries(dir).get(100)!.time).toBe('2026-10-06 07:57:11')
  })

  it('老行无 nodes → 原样透传（不猜）', () => {
    const dir = mkDir('summary-nonodes')
    writeLog(dir, [{ event: 'eval_summary', iter: 0, wver: 'w', time: CLOUD_UTC }])
    expect(readEvalSummaries(dir).get(0)!.time).toBe(CLOUD_UTC)
  })

  it('非该格式（空串 / 已带时区 / 别的写法）→ 原样透传', () => {
    const dir = mkDir('summary-badfmt')
    writeLog(dir, [
      { event: 'eval_summary', iter: 1, wver: 'w', time: '', nodes: { cloud: 1 } },
      {
        event: 'eval_summary',
        iter: 2,
        wver: 'w',
        time: '2026-10-05T21:39:04Z',
        nodes: { cloud: 1 },
      },
      { event: 'eval_summary', iter: 3, wver: 'w', time: '-', nodes: { cloud: 1 } },
    ])
    const out = readEvalSummaries(dir)
    expect(out.get(1)!.time).toBe('')
    expect(out.get(2)!.time).toBe('2026-10-05T21:39:04Z')
    expect(out.get(3)!.time).toBe('-')
  })
})

describe('readLatestEvalGames：summary head 与逐局行各自判节点', () => {
  it('cloud summary head time 转换；逐局行按自己的 node 判（cloud 转 / local 不转）', () => {
    const dir = mkDir('games-mixed')
    writeLog(dir, [
      {
        event: 'eval',
        iter: 10,
        wver: 'wv',
        time: CLOUD_UTC,
        stage: 0,
        seed: 100,
        node: 'cloud',
        outcome: 'gameover',
        win: 0,
      },
      {
        event: 'eval',
        iter: 10,
        wver: 'wv',
        time: LOCAL_STAMP,
        stage: 0,
        seed: 101,
        node: 'local',
        outcome: 'gameover',
        win: 0,
      },
      { event: 'eval_summary', iter: 10, wver: 'wv', time: CLOUD_UTC, nodes: { cloud: 400 } },
    ])
    const v = readLatestEvalGames(dir)!
    expect(v.time).toBe(CLOUD_LOCAL)
    const bySeed = new Map(v.rows.map((r) => [r.seed, r]))
    expect(bySeed.get(100)!.time).toBe(CLOUD_LOCAL)
    expect(bySeed.get(101)!.time).toBe(LOCAL_STAMP)
  })

  it('本机 summary（无 cloud）→ head 与行都原样', () => {
    const dir = mkDir('games-local')
    writeLog(dir, [
      {
        event: 'eval',
        iter: 3,
        wver: 'wv',
        time: LOCAL_STAMP,
        stage: 0,
        seed: 100,
        node: 'mac',
        outcome: 'gameover',
        win: 0,
      },
      {
        event: 'eval_summary',
        iter: 3,
        wver: 'wv',
        time: '2026-10-06 05:40:00',
        nodes: { mac: 200 },
      },
    ])
    const v = readLatestEvalGames(dir)!
    expect(v.time).toBe('2026-10-06 05:40:00')
    expect(v.rows[0]!.time).toBe(LOCAL_STAMP)
  })
})
