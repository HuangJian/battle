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

  it('resolveComponentLog：五个组件均有日志映射；未知组件 null', () => {
    const cfg = JSON.parse(readFileSync(configPath(), 'utf-8')) as Parameters<
      typeof api.resolveComponentLog
    >[1]
    for (const key of [
      'selfNode',
      'hubServer',
      'cloudflared',
      'trainingLoop',
      'workerServe',
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
      expect(api.scanLatestLog(dir, 'workerServe', 'x')).toBeNull()
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
