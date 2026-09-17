/** stop.ts — 组件停止与全停（端口兜底清场在 core/proc）。 */
import { loadConfig } from '../../core/config'
import { warn as logWarn } from '../../core/log'
import { killPid, killPidTree, pidAlive } from '../../core/net'
import { portOwnerPids, stopAllManaged } from '../../core/proc'
import { clearAnyComponent } from '../../core/registry'
import { slotPort } from '../../core/slots'
import { COMPONENT_KILL_TREE } from '../../core/types'
import type { Component, RlConfig } from '../../core/types'
import { releaseTrainerLocks } from '../../launch/cli'
import { entryOf } from './cloud-halt'
import { COMPONENT_LABELS } from './labels'
import { ActionResult, busyKey, done, guard, release } from './result'

// ────────────────────────── 组件停止 ──────────────────────────

/** 停止单个组件（按账本；无登记时按端口兜底清场——与 --kill 同语义）。
 *
 *  fail-closed（plan P1「兜底规则」）：多课时代按端口盲扫 = 杀错课（本仓前科×2），
 *  故 hubServer/workerServe 在**无登记且无课程上下文**时拒绝兜底并响亮告警，
 *  （localWorker 不监听任何端口，无登记就是「未在运行」——无需兜底。）
 *  指引操作员指定课程或走 stopAll（紧急总闸）。selfNode 是全局单例（agent_port），
 *  不受此限。 */
export async function stopComponent(key: Component, course = ''): Promise<ActionResult> {
  // 键必须与 finally 释放的键同源（2026-09-14 事故，同 start.ts）。
  const bk = busyKey('stop', key, course)
  guard(bk)
  try {
    /** 「停止 trainer」的附加收尾：释放 python 侧单实例锁（run_rl / run_bc）。
     *
     *  2026-09-17：账本 pid 与**锁持有者**可以是两个不同进程（崩溃残留 / PID 复用 /
     *  控制台重启竞态）。旧停止路径只杀账本 pid ⇒ 锁里的存活持有者把「停止 → 启动」
     *  永久卡死（python 侧只提示「先停止在跑训练（或删除该锁文件）」，控制台无处可删）。
     *  释放前**核验进程身份**（命令行必须命中本课 run_rl/run_bc），绝不对复用 PID 误杀。 */
    const releaseLocks = async (crs: string): Promise<string> => {
      if (key !== 'trainingLoop') return ''
      const notes = await releaseTrainerLocks(crs)
      return notes.length > 0 ? `；${notes.join('；')}` : ''
    }
    const entry = entryOf(key, course)
    if (entry?.pid) {
      if (pidAlive(entry.pid)) {
        // 带子进程监督器的组件（localWorker）必须整树停：只杀父进程会留下继续轮询 hub
        // 抢 job 的孤儿，「随时启停」形同虚设（判定唯一来源 types.COMPONENT_KILL_TREE）。
        const dead = COMPONENT_KILL_TREE.has(key)
          ? await killPidTree(entry.pid)
          : await killPid(entry.pid)
        if (!dead) return done(false, `${COMPONENT_LABELS[key]} (PID ${entry.pid}) 未能停止`)
      }
      clearAnyComponent(key, course || entry.course || '')
      const notes = await releaseLocks(course || entry.course || '')
      return done(
        true,
        `${COMPONENT_LABELS[key]} 已停止${course ? ` (course=${course})` : ''}${notes}`,
      )
    }
    // 无登记：端口兜底（端口一律经槽位算术，不再手写偏移）
    const ports: Record<string, (cfg: RlConfig, course: string) => number> = {
      selfNode: (c) => c.rl.agent_port,
      hubServer: (c, crs) => slotPort(c, crs, 'hub'),
      workerServe: (c, crs) => slotPort(c, crs, 'push'),
    }
    const portOf = ports[key]
    if (portOf) {
      const known = course || entry?.course || ''
      if (!known && key !== 'selfNode') {
        const msg =
          `${COMPONENT_LABELS[key]} 无注册且未指定课程——拒绝按端口兜底` +
          '（多课程下按端口盲扫可能停错课）；请在指定课程后重试，或用「全部停止」'
        logWarn(`[console] ${msg}`)
        return done(false, msg)
      }
      const cfg = loadConfig()
      const pids = portOwnerPids(portOf(cfg, known))
      for (const pid of pids) await killPid(pid)
      return done(true, pids.length > 0 ? `已按端口兜底停止 ${pids.length} 个进程` : '未在运行')
    }
    const notes = await releaseLocks(course || entry?.course || '')
    return done(true, `${COMPONENT_LABELS[key]} 未在运行${notes}`)
  } catch (e) {
    return done(false, `停止失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(bk)
  }
}

/** 停止全部受管进程（账本 + 端口兜底，复用 CLI --kill 语义）。 */
export async function stopAll(): Promise<ActionResult> {
  guard('stop:all')
  try {
    await stopAllManaged()
    return done(true, '已停止全部受管进程')
  } catch (e) {
    return done(false, `停止失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release('stop:all')
  }
}
