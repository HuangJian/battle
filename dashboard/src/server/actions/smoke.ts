/** smoke.ts — 组件冒烟（启动前门禁，按组件分派）。 */
import path from 'path'
import { loadConfig } from '../../core/config'
import { httpOk, pidAlive } from '../../core/net'
import { LOG_DIR, REPO_ROOT } from '../../core/paths'
import { slotPort } from '../../core/slots'
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
        const hubPort = slotPort(cfg, ctx.course, 'hub')
        const hubOk = await hubServerHealthy(cfg, ctx.course)
        items.push({
          name: 'hub-server /ping',
          passed: hubOk,
          fatal: true,
          detail: hubOk ? `port ${hubPort}` : '未运行或 token 不匹配',
        })
        break
      }
      case 'cloudflared': {
        // 展示路径：per-course 优先，旧单键兜底（R1 读兼容窗口到 P5）。
        const url = entryOf('cloudflared', ctx.course)?.url ?? ''
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
      case 'workerServe': {
        const ping = await httpOk(
          `http://127.0.0.1:${slotPort(cfg, ctx.course, 'push')}/ping`,
          cfg.rl.remote_token,
          3000,
        )
        items.push({ name: '本机伪 GPU 节点 /ping', passed: ping, fatal: false })
        break
      }
      case 'trainingLoop': {
        const alive = pidAlive(entryOf('trainingLoop', ctx.course)?.pid)
        items.push({ name: 'TrainingLoop 进程存活', passed: alive, fatal: false })
        const logPath = path.join(
          LOG_DIR,
          ctx.course || loadConsoleState().course,
          'training-loop.log',
        )
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
