/**
 * supervisor-stale-code.test.ts — 「控制台重启后，在跑组件永远停在旧码上」的护栏。
 *
 * 2026-09-20 事故（用户报障「云机领到 ppo 任务后一直报错」，真因之一）：变更检测监督器
 * （`core/reload.ts`）的 watch 表住**内存**——控制台一重启就清空；重启后的
 * `reconcileWatch()` 把每一个在跑进程**重新基线化**成「现在就绪」（`watch()` 以当下指纹
 * 为基线），于是「这个进程早于最后一次改码」这个事实永久消失：盘上刚加的课程开课闸
 * 对已在跑的 hub-server 永不生效，它照样把**未开课课程**的陈旧 pending job 派给真 GPU
 * worker（云端逐份 D14 拒收/旧码自重启，白烧租约），而控制台一切显示正常。
 *
 * 修复 = 账本记**启动时刻**（`RegistryEntry.startedAt`）＋ 启动对账时先判
 * `runningStaleCode(spec, startedAt)`：晚于启动时刻的哨兵 mtime = 该进程跑的是旧码 ⇒
 * 先重启再接管监督（`server.ts::reconcileWatch`）。
 *
 * 本文件守两件事：① 判据本身（边界：无哨兵/无记录/容差/晚于启动）；② 接线门禁
 * （判据被真正接在启动对账上、且每个 spawn 点都写了 `startedAt`——缺一处 = 那个组件
 * 的旧码即永久合法，UI 无声）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, utimesSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { runningStaleCode } from '../src/core/reload'
import { loadConfig } from '../src/core/config'
import { hubServerSpec, localWorkerSpec, trainerServeSpec, selfNodeSpec } from '../src/stack/specs'
import { resolveVenvPython } from '../src/core/venv'
import type { ProcSpec } from '../src/core/types'

const SRC = (rel: string): string => readFileSync(path.join(DASHBOARD_ROOT, rel), 'utf-8')

/** 最小 spec：只有哨兵与展示名是判据关心的。 */
function specWith(sentinels: string[]): ProcSpec {
  return {
    key: 'hubServer',
    name: 'test',
    cmd: ['true'],
    log: path.join(os.tmpdir(), 'test.log'),
    healthy: async () => true,
    sentinels,
  }
}

describe('runningStaleCode（判据）', () => {
  it('无哨兵 ⇒ 恒 false（第三方二进制没有可比对的磁盘代码身份）', () => {
    expect(runningStaleCode(specWith([]), undefined)).toBe(false)
    expect(runningStaleCode(specWith([]), 1)).toBe(false)
  })

  it('无启动时刻（旧账本条目 / 手工起的进程）⇒ 按旧码处理（接管时重启一次）', () => {
    const f = path.join(mkdtempSync(path.join(os.tmpdir(), 'stale-')), 'x.py')
    writeFileSync(f, 'x', 'utf-8')
    expect(runningStaleCode(specWith([f]), undefined)).toBe(true)
    expect(runningStaleCode(specWith([f]), Number.NaN)).toBe(true)
  })

  it('哨兵晚于启动时刻 = 旧码；早于 = 当前码', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'stale-'))
    const f = path.join(dir, 'hub_server.py')
    writeFileSync(f, 'x', 'utf-8')
    // 启动时刻在文件落盘之后 10s ⇒ 进程跑的就是这份代码
    const startedAfter = Date.now() + 10_000
    expect(runningStaleCode(specWith([f]), startedAfter)).toBe(false)
    // 启动时刻在文件落盘之前 10s ⇒ 文件改过而进程没换
    const startedBefore = Date.now() - 10_000
    expect(runningStaleCode(specWith([f]), startedBefore)).toBe(true)
    // 容差：文件恰好刚落盘（同拍竞态）不因它重启
    expect(runningStaleCode(specWith([f]), Date.now())).toBe(false)
    // 哨兵文件不存在（可选条目缺席）⇒ 无从比对 ⇒ 不判旧码（不因缺文件而重启）
    expect(runningStaleCode(specWith([path.join(dir, 'nope.py')]), Date.now())).toBe(false)
    expect(runningStaleCode(specWith([path.join(dir, 'nope.py')]), 1)).toBe(false)
  })

  it('多个哨兵任一新于启动即旧码', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'stale-'))
    const old = path.join(dir, 'a.py')
    const fresh = path.join(dir, 'b.py')
    writeFileSync(old, 'x', 'utf-8')
    writeFileSync(fresh, 'x', 'utf-8')
    utimesSync(old, new Date(Date.now() - 3600_000), new Date(Date.now() - 3600_000))
    const startedAt = Date.now() - 60_000
    expect(runningStaleCode(specWith([old]), startedAt)).toBe(false)
    expect(runningStaleCode(specWith([old, fresh]), startedAt)).toBe(true)
  })
})

describe('接线门禁：启动对账必须接管旧码进程', () => {
  it('reconcileWatch 判 runningStaleCode 并先 restart 再 watch', () => {
    const src = SRC('src/server/server.ts')
    expect(src).toContain("import { runningStaleCode } from '../core/reload'")
    expect(src).toContain('runningStaleCode(spec,')
    // 判旧码 → restart（拿新 pid）→ watch 新 pid：顺序错了就是把旧码进程重新基线化
    const stale = src.indexOf('runningStaleCode(spec,')
    const restart = src.indexOf('pid = await restart(spec, pid)')
    const watch = src.indexOf('sup.watch(spec, pid)')
    expect(stale).toBeGreaterThan(-1)
    expect(restart).toBeGreaterThan(stale)
    expect(watch).toBeGreaterThan(restart)
    // 启动时刻的数据源 = 账本原件（视图里没有这个字段）
    expect(src).toContain('registryComponents()')
    // startSupervisor 必须把 restart 露出来（对账要用同一份重启实现：整树杀 + 回灌槽位）
    expect(src).toContain(
      'return { sup: createSupervisor(restart, { intervalMs: 5000 }), restart }',
    )
  })

  it('旧码接管**不适用于 cloudflared**（重启隧道 = URL 作废、云机失联）', () => {
    const src = SRC('src/server/server.ts')
    expect(src).toContain("c.key !== 'cloudflared'")
    // 闸必须在 runningStaleCode 之前（判据与被判对象同一行，顺序可读）
    expect(src.indexOf("c.key !== 'cloudflared'")).toBeLessThan(
      src.indexOf('runningStaleCode(spec,'),
    )
  })

  it('每个 spawn 点都写 startedAt（缺一处 = 那个组件的旧码永久合法）', () => {
    // 监督器重启路径
    expect(SRC('src/server/server.ts')).toContain('startedAt: Date.now(),')
    // selfNode / hubServer
    expect(SRC('src/stack/hub.ts')).toContain('startedAt: Date.now()')
    // 共享 trainer
    expect(SRC('src/server/actions/start.ts')).toContain('startedAt: Date.now(),')
    // 本机 worker
    expect(SRC('src/stack/local-worker.ts')).toContain('startedAt: Date.now(),')
  })

  it('待监督的组件都带哨兵（否则判据恒 false，等于没有这道闸）', () => {
    const cfg = loadConfig()
    const venv = resolveVenvPython()
    for (const spec of [
      selfNodeSpec(cfg),
      hubServerSpec(cfg),
      localWorkerSpec(cfg, venv),
      trainerServeSpec(cfg, venv),
    ]) {
      expect(spec.sentinels.length).toBeGreaterThan(0)
    }
    // 具体锚：hub 的代码身份必须包含它的入口（这次事故的主角）
    expect(hubServerSpec(cfg).sentinels.some((f) => f.endsWith('hub_server.py'))).toBe(true)
  })
})
