/** sentinels.ts — 变更检测哨兵清单 + 监控触点（监督器 reload.ts 的数据源）。

 *  - codeSentinels(): 全组件共用的代码身份哨兵（codehash-files.txt SSOT 清单——
 *    它变了 = 代码哈希语义变了，所有受管进程都应重启）。
 *  - pySentinels()/pyEntry(): python 入口的哨兵集（入口 .py 自身 + SSOT 清单）。
 *    Bun/python 都直跑源码，无构建产物——mtime 即代码身份。
 *  - monitorTouch(): 任何受管组件 spawn/重启时落一个触点文件（记录时刻），供
 *    lastMonitorChange() 读取——start.ts 据此判断"仓库代码是否在进程启动后才被
 *    改过"，作为监督哨兵的粗粒度补充。
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs'
import path from 'path'
import { MONITOR_TOUCH, REPO_ROOT } from './paths'

/** 全组件共用哨兵：codehash-files.txt（SSOT 清单，双侧哈希同源）。 */
export function codeSentinels(): string[] {
  return ['tools/agent/codehash-files.txt']
}

/** python 组件哨兵：入口 .py（仓库相对 posix 路径）+ 共用清单。 */
export function pySentinels(...entries: string[]): string[] {
  return [...codeSentinels(), ...entries]
}

/** bun（tools/agent）组件哨兵。 */
export function agentSentinels(entry: string): string[] {
  return [...codeSentinels(), entry]
}

/** 监控触点：记录最近一次受管组件 spawn/重启的时刻。 */
export function monitorTouch(): void {
  try {
    mkdirSync(path.dirname(MONITOR_TOUCH), { recursive: true })
    writeFileSync(MONITOR_TOUCH, JSON.stringify({ ts: Date.now() }), 'utf-8')
  } catch {
    /* 非致命 */
  }
}

/** 最近一次监控触点时刻（0 = 无记录）。 */
export function lastMonitorChange(): number {
  try {
    if (!existsSync(MONITOR_TOUCH)) return 0
    const j = JSON.parse(readFileSync(MONITOR_TOUCH, 'utf-8')) as { ts?: number }
    return typeof j.ts === 'number' ? j.ts : 0
  } catch {
    return 0
  }
}

/** 哨兵文件自某时刻起是否被改过（粗粒度"代码比进程新"判定）。 */
export async function sentinelsChangedSince(files: string[], sinceMs: number): Promise<boolean> {
  for (const rel of files) {
    try {
      const st = await Bun.file(path.join(REPO_ROOT, rel)).stat()
      if (!st) continue
      if (st.mtime.getTime() > sinceMs) return true
    } catch {
      /* missing file — skip */
    }
  }
  return false
}

/** 哨兵文件指纹（mtime+size 拼接；监督器/对比测试用）。 */
export async function sentinelStamp(files: string[]): Promise<string> {
  const parts: string[] = []
  for (const rel of files) {
    try {
      const st = await Bun.file(path.join(REPO_ROOT, rel)).stat()
      parts.push(st ? `${rel}:${st.mtime}:${st.size}` : `${rel}:-`)
    } catch {
      parts.push(`${rel}:-`)
    }
  }
  return parts.join('|')
}
