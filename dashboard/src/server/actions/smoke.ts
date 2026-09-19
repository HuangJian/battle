/** smoke.ts — 组件冒烟（启动前门禁，按组件分派）。 */
import { existsSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { httpOk, pidAlive } from '../../core/net'
import { LOG_DIR, REPO_ROOT } from '../../core/paths'
import { sharedHubPort } from '../../core/slots'
import type { Component } from '../../core/types'
import { hubServerHealthy } from '../../stack/hub'
import { rolloutSmoke, selfNodeSmoke, type SmokeItem, summarizeSmoke } from '../../stack/smoke'
import { entryOf } from './cloud-halt'
import { loadConsoleState } from './console-state'
import { COMPONENT_LABELS, tailLines } from './labels'
import { ActionResult, busyKey, done, guard, release } from './result'
import { StartCtx } from './start'

// ────────────────────────── 组件冒烟 ──────────────────────────

/** 单组件冒烟（各组件子集不同：轻量 ping / 真 rollout 一局）。 */
export async function smokeComponent(key: Component, ctx: StartCtx): Promise<ActionResult> {
  const bk = busyKey('smoke', key, ctx.course)
  guard(bk)
  try {
    const cfg = loadConfig()
    const items: SmokeItem[] = []
    const extraDetail: string[] = []

    switch (key) {
      case 'selfNode': {
        items.push(await selfNodeSmoke(cfg))
        if (ctx.course) {
          items.push(
            await rolloutSmoke(cfg, path.join(REPO_ROOT, 'tmp', ctx.course, 'weights.json')),
          )
        } else {
          items.push({ name: 'rollout 冒烟', passed: true, fatal: false, detail: '未设课程，跳过' })
        }
        break
      }
      case 'hubServer': {
        // 共享 hub：健康判据与课程无关（一个进程服务所有课）；课程表靠盘上发现。
        const hubPort = sharedHubPort(cfg)
        const hubOk = await hubServerHealthy(cfg)
        items.push({
          name: 'hub-server /ping',
          passed: hubOk,
          fatal: true,
          detail: hubOk ? `port ${hubPort}` : '未运行或 token 不匹配',
        })
        break
      }
      case 'cloudflared': {
        // 共享单隧道：登记在 `''` 槽。
        const url = entryOf('cloudflared', '')?.url ?? ''
        if (!url) {
          items.push({ name: 'cloudflared', passed: false, fatal: false, detail: '未建立隧道' })
          break
        }
        const ping = await httpOk(`${url}/ping`, cfg.rl.remote_token, 10000)
        items.push({
          name: `隧道 ping (${url})`,
          passed: ping,
          fatal: false,
          detail: ping ? undefined : 'edge 在线但出网劣化，或隧道不可达',
        })
        break
      }
      case 'localWorker': {
        // 本机 PPO worker（独立进程，poll 共享 hub）：存活 + 轮询目标可达 + 日志尾。
        // 「hub 通不通」是它能不能领到活的唯一外部依赖，故按 fatal:false 提示（worker
        // 会自己重连；hub 后起也能自愈）。
        // 共享实例（2026-09-19）：`entryOf` 内部走 scopeOf 归一为 `''` 槽——冒烟问的是
        // 「那份唯一的进程在不在跑」，与当前查看的课程无关。
        const entry = entryOf('localWorker')
        items.push({ name: 'local-worker 进程存活', passed: pidAlive(entry?.pid), fatal: true })
        const hubPort = sharedHubPort(cfg)
        const hubUp = await httpOk(`http://127.0.0.1:${hubPort}/ping`, cfg.rl.remote_token, 3000)
        items.push({
          name: 'poll 目标 hub-server /ping',
          passed: hubUp,
          fatal: false,
          detail: hubUp ? `port ${hubPort}` : 'hub 未就绪（worker 会持续重试轮询）',
        })
        // 日志也是**唯一**一份（共享实例不再按课程分目录）。
        const logPath = entry?.log ?? path.join(LOG_DIR, 'local-worker.log')
        extraDetail.push(...tailLines(logPath, 6).map((l) => `日志│ ${l}`))
        break
      }
      case 'trainingLoop': {
        // 共享 trainer（2026-09-19 / R3-5）：账本槽恒 `''`（entryOf 内部走 scopeOf）——
        // 存活是**进程级**一件事，冒烟说的也是这件事：它在不在跑。
        const alive = pidAlive(entryOf('trainingLoop')?.pid)
        items.push({ name: '共享 trainer 进程存活', passed: alive, fatal: false })
        // 日志优先取**本课镜像**（serve 的行路由写的，与控制台按课读的其它面同源），
        // 镜像还没出现时回落到进程自己的 stdout（`trainer-cluster.log`）。
        const mirror = path.join(
          LOG_DIR,
          ctx.course || loadConsoleState().course || 'nocourse',
          'training-loop.log',
        )
        const logPath = existsSync(mirror) ? mirror : (entryOf('trainingLoop')?.log ?? mirror)
        extraDetail.push(...tailLines(logPath, 6).map((l) => `日志│ ${l}`))
        break
      }
    }

    const passed = summarizeSmoke(items)
    const detail = [
      ...items.map((i) => `${i.passed ? '✅' : '❌'} ${i.name}${i.detail ? ` — ${i.detail}` : ''}`),
      ...extraDetail,
    ]
    return done(
      passed,
      passed ? `${COMPONENT_LABELS[key]} 冒烟通过` : `${COMPONENT_LABELS[key]} 冒烟未通过`,
      detail,
    )
  } catch (e) {
    return done(false, `冒烟失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(bk)
  }
}
