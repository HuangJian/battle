/** stop.ts — 组件停止与全停（端口兜底清场在 core/proc）。 */
import { loadConfig } from '../../core/config'
import { warn as logWarn } from '../../core/log'
import { killPid, killPidTree, pidAlive } from '../../core/net'
import { portOwnerPids, stopAllManaged } from '../../core/proc'
import { clearAnyComponent, isSharedComponent, scopeOf } from '../../core/registry'
import { sharedHubPort, sharedTunnelMetricsPort, slotPort } from '../../core/slots'
import { COMPONENT_KILL_TREE } from '../../core/types'
import type { Component, RlConfig } from '../../core/types'
import { releaseClusterLock, releaseTrainerLocks } from '../../launch/cli'
import { supersedeLegacyInstances } from '../../stack/hub'
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
export async function stopComponent(key: Component, courseArg = ''): Promise<ActionResult> {
  // 槽位归一（共享组件恒 `''`）：调用方传的课程是**视图语境**（「我在看哪门课」），
  // 而 hub/隧道是共享的——不归一会拿课程槽去查/键控一个不属于任何课的条目。
  const course = scopeOf(key, courseArg)
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
      // trainer 的锁有三层：**进程级单实例锁**（共享 trainer 自己，2026-09-19）+ 它开课时为
      // 每门课取的按课锁（RL=run_rl / BC=run_bc）。三层都要清：账本 pid 与锁持有者可以是两个
      // 不同进程（崩溃残留 / PID 复用），留着任一把都会把「停止 → 启动」永久卡死。
      const notes = [...(await releaseTrainerLocks(crs)), await releaseClusterLock()]
      const kept = notes.filter(Boolean)
      return kept.length > 0 ? `；${kept.join('；')}` : ''
    }
    /** 停**共享组件** = 停掉那一个进程：共享实例是唯一形状，但旧形状的每课条目仍可能
     *  存活（换代之前的残留）——它们是同一个角色，一起收掉才算「停了它」
     *  （与启动时的换代接管同一把尺子，见 stack/hub.ts::supersedeLegacyInstances）。 */
    const stopLegacy = async (): Promise<string> => {
      if (!isSharedComponent(key)) return ''
      const taken = await supersedeLegacyInstances(key)
      return taken.length > 0 ? `；同时收掉旧形状的每课实例（原属 ${taken.join(', ')}）` : ''
    }
    const entry = entryOf(key, course)
    if (entry?.pid) {
      if (pidAlive(entry.pid)) {
        // 带子进程监督器的组件（localWorker）必须整树停：只杀父进程会留下继续轮询 hub
        // 抢 job 的孤儿，「随时启停」形同虚设（判定唯一来源 types.COMPONENT_KILL_TREE）。
        // trainer 同理：共享 trainer 之下有 rollout / 本机 PPO 子进程，只杀父进程就留下
        // 还在写同一批 traj 的孤儿（比 localWorker 更贵——它们会真跑一轮）。
        const dead = COMPONENT_KILL_TREE.has(key)
          ? await killPidTree(entry.pid)
          : await killPid(entry.pid)
        if (!dead) return done(false, `${COMPONENT_LABELS[key]} (PID ${entry.pid}) 未能停止`)
      }
      clearAnyComponent(key, course || entry.course || '')
      const notes = await releaseLocks(course || entry.course || '')
      const legacy = await stopLegacy()
      // 共享组件的行为语义必须在结果里说出来——不然操作员以为只停了「当前这门课」：
      // trainer ⇒ 所有课程的训练随之停止（想停单门课走调度器卡片的「暂停」，只影响调度，
      // 队列与账本不动）；本机 worker ⇒ 本机不再执行任何课程的 PPO job（云端 worker 照常）。
      const scopeNote =
        key === 'trainingLoop'
          ? '（共享 trainer：所有课程的训练随之停止）'
          : key === 'localWorker'
            ? '（共享本机 worker：本机不再执行任何课程的 PPO job，云端 worker 不受影响）'
            : ''
      return done(
        true,
        `${COMPONENT_LABELS[key]} 已停止${scopeNote}${course ? ` (course=${course})` : ''}${notes}${legacy}`,
      )
    }
    // 无登记：端口兜底（端口一律经算术函数，不再手写偏移）
    // hub/metrics 是**共享**端口：不需要也不允许拿课程去推（旧实现按课推 = 推错端口）。
    const ports: Record<string, (cfg: RlConfig, course: string) => number> = {
      selfNode: (c) => c.rl.agent_port,
      hubServer: (c) => sharedHubPort(c),
      cloudflared: (c) => sharedTunnelMetricsPort(c),
      workerServe: (c, crs) => slotPort(c, crs, 'push'),
    }
    const portOf = ports[key]
    if (portOf) {
      const known = course || entry?.course || ''
      const shared = isSharedComponent(key)
      if (!known && key !== 'selfNode' && !shared) {
        const msg =
          `${COMPONENT_LABELS[key]} 无注册且未指定课程——拒绝按端口兜底` +
          '（多课程下按端口盲扫可能停错课）；请在指定课程后重试，或用「全部停止」'
        logWarn(`[console] ${msg}`)
        return done(false, msg)
      }
      const cfg = loadConfig()
      // 共享组件的端口兜底是**全仓唯一**的那一个端口，与课程无关（传空串避免误用槽位）。
      const pids = portOwnerPids(portOf(cfg, shared ? '' : known))
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
