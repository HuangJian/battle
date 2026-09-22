/**
 * server-api-logs.test.ts — readLogTail / logTail / scanLatestLog / resolveComponentLog / componentLogPayload + §373 GBK 逐行容错解码
 *
 * 分层：src/server/api/logs.ts + component-meta.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import os from 'os'
import path from 'path'
import { LOG_DIR, NN_TRAINING } from '../src/core/paths'
import { api } from './helpers/console-fixture'
import { configPath } from '../src/core/paths'
import { latestIterFromLedgerTail } from '../src/web/view'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'fs'

describe('console/log viewer (§348 补 2)', () => {
  it('readLogTail：文件尾部窗口 + maxLines 截断 + 缺文件安全', () => {
    const missing = api.readLogTail('tmp/no-such-log-xyz.log', 50)
    expect(missing.exists).toBe(false)
    expect(missing.lines).toEqual([])
    // sampler-agent.log 在真实仓库中通常存在（历史运行产物）；若存在则行数受 maxLines 约束
    const real = api.readLogTail('tmp/sampler-agent.log', 30)
    if (real.exists) {
      expect(real.lines.length).toBeLessThanOrEqual(30)
      expect(real.fileSize).toBeGreaterThan(0)
    }
  })

  it('readLogTail：maxLines=all 读全部行，小文件不截断（§371）', () => {
    const rel = 'tmp/logtail-all-371.log'
    const p = path.join(NN_TRAINING, rel)
    mkdirSync(path.dirname(p), { recursive: true })
    const lines = Array.from({ length: 50 }, (_, i) => `line-${i}`)
    writeFileSync(p, lines.join('\n') + '\n', 'utf-8')
    try {
      const t = api.readLogTail(rel, 'all')
      expect(t.exists).toBe(true)
      expect(t.truncated).toBe(false)
      expect(t.lines).toEqual(lines)
      expect(t.totalLines).toBe(50) // 顶部「共 N 行」= 文件总行数（§372）
      // 数字模式仍然只取尾 N 行
      const t5 = api.readLogTail(rel, 5)
      expect(t5.lines).toEqual(lines.slice(-5))
      // 缺文件：totalLines null
      expect(api.readLogTail('tmp/no-such-log-xyz.log', 50).totalLines).toBeNull()
    } finally {
      rmSync(p, { force: true })
    }
  })

  it('readLedgerTail：长行不截断——iteration 事件带遥测（>500 字符）也能解析出轮次', () => {
    // 2026-09-20 实测（x20-steady/c6-chip 真实账本）：`iteration` 事件单行 >1.2KB，
    // 经 readLogTail 的展示截断（`${slice(0,500)}…`）后 JSON 不可解析 ⇒ 总览「轮次」列
    // 对每门课恒显 `—`、贡献基准轮读不到。账本读法必须原样返回。
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-ledger-'))
    const p = path.join(dir, 'training_log.jsonl')
    const long = JSON.stringify({
      event: 'iteration',
      iter: 42,
      time: '2026-09-20 20:00:57',
      wire: { protocol: 'http2', edge_ip: '4', pad: 'x'.repeat(900) },
    })
    writeFileSync(p, `${long}\n`, 'utf-8')
    try {
      expect(long.length).toBeGreaterThan(500)
      const shown = api.readLogTail(p, 600).lines
      expect(shown[0]!.endsWith('…')).toBe(true)
      expect(latestIterFromLedgerTail(shown)).toBeNull() // 展示口径：截断即不可解析（旧 bug 现场）
      expect(latestIterFromLedgerTail(api.readLedgerTail(p, 600))).toBe(42)
      // 短行不受影响（两种读法一致）
      const short = JSON.stringify({ event: 'iteration', iter: 7 })
      writeFileSync(p, `${short}\n`, 'utf-8')
      expect(api.readLedgerTail(p, 600)).toEqual([short])
      expect(api.readLedgerTail(path.join(dir, 'no-such-ledger.jsonl'), 600)).toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('日志 GBK 乱码修复（§373）：混合 UTF-8/GBK 行逐行容错解码，不误伤其它行', () => {
    // 「超时（瞬时连接被拒），重试 5.0s 之后」的 GBK 字节（python gbk encode 实测）
    const gbkB64 = 's6zKsaOoy7LKscGsvdOxu77co6mjrNbYytQgNS4wcyDWrrrz'
    const gbkBytes = Uint8Array.from(atob(gbkB64), (c) => c.charCodeAt(0))
    const rel = 'tmp/logtail-gbk-373.log'
    const p = path.join(NN_TRAINING, rel)
    mkdirSync(path.dirname(p), { recursive: true })
    const buf = Buffer.concat([
      Buffer.from('[09:01:53] wait_job: job bb11e73f1d2c327d HTTP 530 '),
      Buffer.from(gbkBytes),
      Buffer.from('\n'),
      Buffer.from('[09:01:55] [sampler-agent] task ok utf8 中文正常行\n'),
    ])
    writeFileSync(p, buf)
    try {
      const t = api.readLogTail(rel, 'all')
      expect(t.lines[0]).toContain('超时（瞬时连接被拒）')
      expect(t.lines[0]).not.toContain('\uFFFD') // 无替换符乱码残留
      expect(t.lines[1]).toBe('[09:01:55] [sampler-agent] task ok utf8 中文正常行') // UTF-8 行不受影响
      // dashboard 卡的 logTail 同步修复
      const tail = api.logTail(rel, 5)
      expect(tail[0]).toContain('超时')
    } finally {
      rmSync(p, { force: true })
    }
  })

  it('resolveComponentLog：受管组件均有日志映射；未知组件 null', () => {
    const cfg = JSON.parse(readFileSync(configPath(), 'utf-8')) as Parameters<
      typeof api.resolveComponentLog
    >[1]
    // 受管组件全集（ALL_COMPONENTS）：本机伪节点 2026-09-19 已退出（它只服务冒烟预演，
    // 没有卡片/日志页入口）。
    for (const key of [
      'selfNode',
      'hubServer',
      'cloudflared',
      'localWorker',
      'trainingLoop',
    ] as const) {
      expect(api.resolveComponentLog(key, cfg, 'p4-horizon')).toBeTruthy()
    }
    expect(api.resolveComponentLog('nope' as never, cfg, 'x')).toBeNull()
  })

  it('scanLatestLog（§374/§381）：cloudflared 动态文件名按 mtime 取最新，忽略无关文件', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-logscan-'))
    try {
      const stale = path.join(dir, 'cloudflared-2026-09-08T04-00-00-a1.log')
      const fresh = path.join(dir, 'cloudflared-2026-09-08T05-00-00-a1.log')
      writeFileSync(stale, 'stale\n', 'utf-8')
      writeFileSync(fresh, 'fresh\n', 'utf-8')
      writeFileSync(path.join(dir, 'hub-server.out'), 'noise\n', 'utf-8')
      // 指定 mtime（utimes 确定性：stale < fresh），不依赖写入顺序
      utimesSync(stale, new Date('2026-09-08T05:00:00Z'), new Date('2026-09-08T05:00:00Z'))
      utimesSync(fresh, new Date('2026-09-08T06:00:00Z'), new Date('2026-09-08T06:00:00Z'))
      expect(api.scanLatestLog(dir, 'cloudflared', 'x')).toBe(fresh)
      // 无匹配文件 → null；目录不存在 → null（不抛）
      expect(api.scanLatestLog(dir, 'localWorker', 'x')).toBeNull()
      const missing = path.join(os.tmpdir(), 'bcity-logscan-no-such-dir-xyz')
      expect(api.scanLatestLog(missing, 'selfNode', 'x')).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('scanLatestLog（§374/§381）：trainingLoop 扫课程子目录与 course 直连路径', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-logscan2-'))
    try {
      const courseDir = path.join(dir, 'p3-vk1')
      const otherDir = path.join(dir, 'ep60')
      mkdirSync(courseDir, { recursive: true })
      mkdirSync(otherDir, { recursive: true })
      const a = path.join(courseDir, 'training-loop.log')
      const b = path.join(otherDir, 'training-loop.log')
      writeFileSync(a, 'a\n', 'utf-8')
      writeFileSync(b, 'b\n', 'utf-8')
      utimesSync(a, new Date('2026-09-08T05:00:00Z'), new Date('2026-09-08T05:00:00Z'))
      utimesSync(b, new Date('2026-09-08T06:00:00Z'), new Date('2026-09-08T06:00:00Z'))
      // 任意课程目录里最新的 training-loop.log（ep60 新）
      expect(api.scanLatestLog(dir, 'trainingLoop', '')).toBe(b)
      // course 直连路径存在且比其它都新 → 优先
      utimesSync(a, new Date('2026-09-08T07:00:00Z'), new Date('2026-09-08T07:00:00Z'))
      expect(api.scanLatestLog(dir, 'trainingLoop', 'p3-vk1')).toBe(a)
      // 目录不存在 → null
      expect(
        api.scanLatestLog(
          path.join(os.tmpdir(), 'bcity-logscan2-no-dir'),
          'trainingLoop',
          'p3-vk1',
        ),
      ).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('findLatestLog 的扫描根跟随 BCITY_TMP_LOGS_DIR（测试不再扫真实 tmp/）', () => {
    // 为什么钉这条：扫描根若是常量 LOG_DIR（= 仓根 tmp/），单测就会去看这台机器此刻
    // 谁在训——本机有活的 `tmp/<别课>/training-loop.log` 时，「按课程解析」退化成「别课最新」，
    // 而用例要断言的是前者（2026-09-22 server-api-course-switch 因此红）。
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-logscan-root-'))
    const prev = process.env.BCITY_TMP_LOGS_DIR
    try {
      const cDir = path.join(dir, 'course-b')
      mkdirSync(cDir, { recursive: true })
      const loopLog = path.join(cDir, 'training-loop.log')
      writeFileSync(loopLog, 'b\n', 'utf-8')
      const cf = path.join(dir, 'cloudflared-2026-09-22T00-00-00-a1.log')
      writeFileSync(cf, 'cf\n', 'utf-8')
      process.env.BCITY_TMP_LOGS_DIR = dir
      expect(api.findLatestLog('trainingLoop', 'course-b')).toBe(loopLog)
      // 能返回这个临时目录里的文件 ⇒ 扫的确实是重定向后的根（真实 tmp/ 里永远找不到它）
      expect(api.findLatestLog('cloudflared', 'x')).toBe(cf)
    } finally {
      if (prev === undefined) delete process.env.BCITY_TMP_LOGS_DIR
      else process.env.BCITY_TMP_LOGS_DIR = prev
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('componentLogPayload：已知组件返回载荷；未知组件 null', async () => {
    const p = await api.componentLogPayload('selfNode', 50)
    expect(p).not.toBeNull()
    expect(p!.component).toBe('selfNode')
    // 2026-09-08 双 tmp 统一：组件日志统一落到 LOG_DIR = 仓库根 tmp/（绝对路径）
    expect(p!.log).toBe(path.join(LOG_DIR, 'sampler-agent.log'))
    expect(Array.isArray(p!.lines)).toBe(true)
    expect(await api.componentLogPayload('nope' as never, 50)).toBeNull()
  })
})
