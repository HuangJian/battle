/** proc.ts — 后台组件进程 spawn / 停止 / 日志（全 Bun 原生 API）。

 *  - spawnBg: detached + windowsHide——Bun 在 Windows 上默认 kill-on-close（Job
 *    Object），脚本一退子进程全灭；detached 脱离后存活，windowsHide 杜绝黑窗。
 *    stdout/stderr fd 级直写日志文件（§339：cloudflared shim 不透传句柄的坑靠
 *    组件各自 logfile 语义兜底）。
 *  - stopAll: 先按账本精确停止，再按端口兜底清场（只认端口，不认出身——agent
 *    自重启遗留/手动启动/登记丢失的进程由这层兜住）。端口→PID 是全工具链唯一
 *    触碰 OS 进程表的点（Bun 无原生 API）：Windows netstat -ano / POSIX lsof。
 */

import { closeSync, mkdirSync, openSync } from 'fs'
import path from 'path'
import { CONFIG_PATH } from './paths'
import { loadConfig } from './config'
import { clearRegistry, registryComponents } from './registry'
import { killPid, pidAlive, portListen, waitUntil } from './net'
import { info, log, ok, warn } from './log'
import type { ProcSpec, RlConfig } from './types'

export interface SpawnBgResult {
  pid: number
  logFile: string
}

/** 后台启动组件进程：stdout/stderr fd 级直写日志；detached 脱离父进程 Job Object
 *  存活；windowsHide 杜绝控制台黑窗口。 */
export function spawnBg(
  cmd: string[],
  opts: { cwd?: string; env?: Record<string, string>; log?: string } = {},
): SpawnBgResult {
  if (opts.log) mkdirSync(path.dirname(opts.log), { recursive: true })
  const fd = opts.log ? openSync(opts.log, 'a') : undefined
  const proc = Bun.spawn({
    cmd,
    cwd: opts.cwd,
    env: { ...process.env, ...opts.env },
    stdin: 'ignore',
    stdout: fd ?? 'ignore',
    stderr: fd ?? 'ignore',
    detached: true,
    windowsHide: true,
  })
  // 写句柄已由子进程继承，本侧即刻关闭
  if (fd !== undefined) closeSync(fd)
  proc.unref()
  return { pid: proc.pid, logFile: opts.log ?? '' }
}

/** 端口占用者 PID 清单（--kill / 兜底清场专用）。Bun 没有端口→PID 的原生 API，
 *  这里是全工具链唯一触碰 OS 进程表的点：Windows 走 netstat -ano（LISTENING 行
 *  尾列即 PID），POSIX 走 lsof。 */
export function portOwnerPids(port: number): number[] {
  try {
    if (process.platform === 'win32') {
      const out = Bun.spawnSync(['netstat', '-ano']).stdout.toString()
      const pids = new Set<number>()
      for (const line of out.split('\n')) {
        if (!line.includes('LISTENING')) continue
        const cols = line.trim().split(/\s+/)
        // TCP  本地地址  远程地址  状态  PID
        if (cols.length >= 5 && cols[1]!.endsWith(`:${port}`)) {
          const pid = Number(cols[4])
          if (Number.isInteger(pid) && pid > 0) pids.add(pid)
        }
      }
      return [...pids]
    }
    const out = Bun.spawnSync(['lsof', '-t', `-i:${port}`, '-s', 'TCP:LISTEN']).stdout.toString()
    return [
      ...new Set(
        out
          .split(/\s+/)
          .map(Number)
          .filter((n) => Number.isInteger(n) && n > 0),
      ),
    ]
  } catch {
    return []
  }
}

/** 停止所有登记的受管进程 + 端口兜底清场（用户指令 2026-09-05：不管进程从哪来，
 *  占着端口就杀）。 */
export async function stopAllManaged(): Promise<void> {
  log('停止所有受管进程...')
  const entries = registryComponents()
  let attempted = 0
  await Promise.all(
    entries.map(async ([name, entry]) => {
      attempted++
      if (!pidAlive(entry.pid)) {
        info(`${name} (PID ${entry.pid}) 已不在运行`)
        return
      }
      const dead = await killPid(entry.pid)
      if (dead) ok(`${name} (PID ${entry.pid}) 已停止`)
      else warn(`${name} (PID ${entry.pid}) 未能停止`)
    }),
  )
  clearRegistry()

  // 端口兜底清场：登记表只覆盖本工具链启动的组件；agent 自重启遗留、手动启动、
  // 登记丢失的进程由这一层兜住——只认端口，不认出身。
  let config: RlConfig | null = null
  try {
    config = loadConfig()
  } catch {
    /* no config — skip port sweep */
  }
  if (config) {
    for (const port of [config.rl.hub_port, config.rl.agent_port]) {
      for (const pid of portOwnerPids(port)) {
        if (pid === process.pid) continue
        await killPid(pid)
        ok(`端口 ${port} 占用进程 (PID ${pid}) 已终止`)
      }
    }
    // 端口释放以探测为准，不做固定等待
    const freed = await waitUntil(
      async () =>
        !(await portListen(config!.rl.hub_port)) && !(await portListen(config!.rl.agent_port)),
      5000,
      300,
    )
    if (freed) {
      ok('所有端口已释放')
    } else {
      warn(
        `仍有端口占用: hub=${config.rl.hub_port} agent=${config.rl.agent_port}（终止失败或权限不足，请手动排查）`,
      )
    }
  }
  if (attempted === 0) info('账本中无进程记录（端口已由兜底清场处理）')
  // 提示 config 路径（日志可读性）
  void CONFIG_PATH
}

/** 由 ProcSpec spawn 受管组件并登记（监督/重启/kill 的统一入口）。 */
export function launchSpec(spec: ProcSpec): SpawnBgResult {
  const r = spawnBg(spec.cmd, { cwd: spec.cwd, env: spec.env, log: spec.log })
  info(`${spec.name} 已启动 (PID ${r.pid}, log ${path.basename(spec.log)})`)
  return r
}
