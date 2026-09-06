/** smoke.ts — 轻量级冒烟测试（三种启动模式共用的"先冒烟后放行"门禁）。

 *  - containerSmoke(): 纯计算回环——BCV2 容器打包/解包逐字节对账，验证工具链
 *    侧序列化契约（复用 tools/sim/pack-container.ts 生产实现），不依赖任何服务。
 *  - weightsSmoke(): 训练产物存在性 + JSON 可解析 + 架构字段。
 *  - rlConfigSmoke(): rl-config 契约（hub/agent 端口、token、nodes 条目）。
 *  - rolloutSmoke(): self 节点端到端（weights → task → result，真采集链路）。
 *  各模式启动前跑对应子集；硬失败 = 不放行（exit 非零由调用方决定）。
 */

import { readFileSync } from 'fs'
import path from 'path'
import { gunzipSync } from 'zlib'
import { buildPack, PACK_MAGIC } from '../sim/pack-container'
import { REPO_ROOT } from './paths'
import { fail, info, log, ok, warn } from './log'
import { httpOk, sha256Hex } from './net'
import { portListen } from './net'
import type { RlConfig } from './types'

/** 单项冒烟结果。 */
export interface SmokeItem {
  name: string
  passed: boolean
  /** 硬失败 = 启动放行被否决；软失败 = 降级容忍（打警告继续）。 */
  fatal: boolean
  detail?: string
}

/** 纯计算冒烟：BCV2 打包/解包回环（gzip 外壳 + magic + headerLen + headerJSON）。 */
export function containerSmoke(): SmokeItem {
  try {
    const report = { stage: 0, seed: 42, smoke: true, games: 1 }
    const entries = [
      { name: 'obs.npy', data: Buffer.from([1, 2, 3]) },
      { name: 'done.npy', data: Buffer.from([0]) },
    ]
    const packed = buildPack(report, entries) // buildPack 已做 gzip 外壳
    if (packed[0] !== 0x1f || packed[1] !== 0x8b) throw new Error('gzip magic missing')
    const frame = gunzipSync(new Uint8Array(packed))
    const dv = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
    if (dv.getUint32(0, false) !== PACK_MAGIC) throw new Error('BCV2 magic mismatch')
    const hlen = dv.getUint32(4, false)
    const header = JSON.parse(new TextDecoder().decode(frame.subarray(8, 8 + hlen))) as {
      manifest?: unknown
      fmt?: string
    }
    if (header.fmt !== 'bcv2') throw new Error('fmt != bcv2')
    if (JSON.stringify(header.manifest) !== JSON.stringify(report)) {
      throw new Error('manifest roundtrip mismatch')
    }
    return { name: 'BCV2 容器回环', passed: true, fatal: true }
  } catch (e) {
    return {
      name: 'BCV2 容器回环',
      passed: false,
      fatal: true,
      detail: e instanceof Error ? e.message : String(e),
    }
  }
}

/** rl-config 契约冒烟：端口/token/nodes 条目齐全。 */
export function rlConfigSmoke(cfg: RlConfig): SmokeItem {
  const problems: string[] = []
  if (!cfg.rl?.hub_port || !cfg.rl?.agent_port) problems.push('rl.hub_port / rl.agent_port 缺失')
  if (!cfg.rl?.remote_token) problems.push('rl.remote_token 缺失')
  if (!Array.isArray(cfg.nodes) || cfg.nodes.length === 0) problems.push('nodes 为空')
  else {
    const enabled = cfg.nodes.filter((n) => n.enabled)
    if (enabled.length === 0) problems.push('无 enabled 节点')
    for (const n of enabled) {
      if (!n.url) problems.push(`节点 ${n.id} 缺 url`)
      if (!n.authKey) problems.push(`节点 ${n.id} 缺 authKey`)
    }
  }
  if (problems.length > 0) {
    return { name: 'rl-config 契约', passed: false, fatal: true, detail: problems.join('; ') }
  }
  return { name: 'rl-config 契约', passed: true, fatal: true }
}

/** 训练产物冒烟：weights.json 存在 + 可解析 + 有 arch 字段（可选——文件缺失返回
 *  passed=true 且 detail 说明跳过，由调用方按模式决定是否要求存在）。 */
export function weightsSmoke(weightsPath: string, required: boolean): SmokeItem {
  if (!path.isAbsolute(weightsPath)) weightsPath = path.join(REPO_ROOT, weightsPath)
  if (!readFileSyncExists(weightsPath)) {
    return required
      ? {
          name: `权重文件 ${path.basename(weightsPath)}`,
          passed: false,
          fatal: true,
          detail: '文件不存在',
        }
      : {
          name: `权重文件 ${path.basename(weightsPath)}`,
          passed: true,
          fatal: false,
          detail: '不存在（跳过）',
        }
  }
  try {
    const j = JSON.parse(readFileSync(weightsPath, 'utf-8')) as Record<string, unknown>
    if (!j.arch && !j.meta && !j.samples) {
      warn(`权重文件结构非典型（无 arch/meta/samples 字段）: ${weightsPath}`)
    }
    return { name: `权重文件 ${path.basename(weightsPath)}`, passed: true, fatal: required }
  } catch (e) {
    return {
      name: `权重文件 ${path.basename(weightsPath)}`,
      passed: false,
      fatal: true,
      detail: `解析失败: ${e instanceof Error ? e.message : e}`,
    }
  }
}

function readFileSyncExists(p: string): boolean {
  try {
    readFileSync(p)
    return true
  } catch {
    return false
  }
}

/** 权重文件 sha256（下发前指纹对账用）。 */
export async function weightsFingerprint(weightsPath: string): Promise<string> {
  const buf = readFileSync(weightsPath)
  const copy = new Uint8Array(buf.byteLength)
  copy.set(buf)
  return sha256Hex(copy)
}

// ────────────────────────── rollout 冒烟（端到端，复用 hub-start §339 语义） ──────────────────────────

/** 异步任务取包：轮询 /v1/result 直到拿到容器字节（200）| 失败（500）| 超时（null）。 */
async function pollAsyncResult(
  node: { id: string; url: string; authKey: string },
  iterId: string,
  timeoutMs = 30000,
): Promise<ArrayBuffer | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const rResp = await fetch(`${node.url}/v1/result?iterId=${iterId}&stage=0&seed=42`, {
        headers: { Authorization: `Bearer ${node.authKey}` },
      })
      if (rResp.status === 200) return await rResp.arrayBuffer()
      if (rResp.status === 500) {
        const body = (await rResp.json()) as Record<string, unknown>
        fail(`${node.id} 任务失败: ${body.error}`)
        return null
      }
      // 202 = 仍在跑，404 = 过期；继续轮询直到超时
    } catch {
      /* transient network error, keep polling */
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  return null
}

/** 解包结果容器头部（manifest + fileCount）：strip 前导空格 → gzip → BCV2 | v1 JSON。 */
function peekContainer(buf: ArrayBuffer): { manifest: Record<string, unknown>; fileCount: number } {
  let bytes = new Uint8Array(buf)
  while (bytes.length > 0 && bytes[0] === 0x20) bytes = bytes.subarray(1)
  if (bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    bytes = Bun.gunzipSync(bytes)
  }
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const MAGIC_BCV2 = 0x42435632 // 'BCV2'
  if (bytes.length >= 8 && dv.getUint32(0, false) === MAGIC_BCV2) {
    const hlen = dv.getUint32(4, false)
    const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + hlen))) as {
      manifest?: Record<string, unknown>
      files?: unknown
    }
    const files = header.files
    const count = Array.isArray(files)
      ? files.length
      : files && typeof files === 'object'
        ? Object.keys(files).length
        : 0
    return { manifest: header.manifest ?? {}, fileCount: count }
  }
  const payload = JSON.parse(new TextDecoder().decode(bytes)) as {
    manifest?: Record<string, unknown>
    files?: unknown
  }
  const count =
    payload.files && typeof payload.files === 'object' ? Object.keys(payload.files).length : 0
  return { manifest: payload.manifest ?? {}, fileCount: count }
}

/** 单节点 rollout 冒烟（weights → task → result）。 */
async function rolloutSmokeNode(
  node: { id: string; url: string; authKey: string },
  weightsBytes: Uint8Array<ArrayBuffer>,
  wver: string,
): Promise<boolean> {
  log(`  向 ${node.id} 派发 rollout 测试局...`)
  try {
    const wResp = await fetch(`${node.url}/v1/weights`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${node.authKey}`,
        'X-Iter-Id': 'smoke-it0',
        'X-Weights-Sha256': wver,
        'Content-Encoding': 'gzip',
        'Content-Type': 'application/octet-stream',
      },
      body: Bun.gzipSync(weightsBytes),
      signal: AbortSignal.timeout(60000),
    })
    if (wResp.status !== 200 && wResp.status !== 204) {
      const body = await wResp.text()
      fail(`${node.id} 权重下发失败: HTTP ${wResp.status} ${body.slice(0, 100)}`)
      return false
    }

    const taskUrl = `${node.url}/v1/task?iterId=smoke-it0&wver=${wver}&stage=0&seed=42&maxTicks=1200&difficulty=hard`
    const tResp = await fetch(taskUrl, {
      headers: {
        Authorization: `Bearer ${node.authKey}`,
        'x-async': '1',
      },
      signal: AbortSignal.timeout(10000),
    })

    let resultBuf: ArrayBuffer | null = null
    if (tResp.status === 200) {
      resultBuf = await tResp.arrayBuffer()
    } else if (tResp.status === 202) {
      resultBuf = await pollAsyncResult(node, 'smoke-it0')
    } else {
      const body = await tResp.text()
      fail(`${node.id} 任务提交失败: HTTP ${tResp.status} ${body.slice(0, 100)}`)
      return false
    }

    if (!resultBuf) {
      fail(`${node.id} 任务超时（30s 无结果）`)
      return false
    }

    const { manifest, fileCount } = peekContainer(resultBuf)
    const outcome = manifest.outcome ?? '?'
    const ticks = manifest.ticks ?? 0
    ok(`${node.id} rollout 冒烟通过: outcome=${outcome} ticks=${ticks} files=${fileCount}`)
    return true
  } catch (e) {
    fail(`${node.id} rollout 冒烟失败: ${e}`)
    return false
  }
}

/** rollout 冒烟（全部 enabled 节点并行）。self 节点是本地采集底线，失败即不通过；
 *  其它节点（mac 等）降级容忍。 */
export async function rolloutSmoke(cfg: RlConfig, weightsPath: string): Promise<SmokeItem> {
  log('运行 rollout 冒烟测试...')
  if (!readFileSyncExists(weightsPath)) {
    warn(`权重文件不存在 (${weightsPath})，跳过 rollout 冒烟`)
    return { name: 'rollout 冒烟', passed: true, fatal: false, detail: '无权重，跳过' }
  }
  const raw = readFileSync(weightsPath)
  const weightsBytes = new Uint8Array(raw.byteLength)
  weightsBytes.set(raw)
  const wver = await weightsFingerprint(weightsPath)

  const enabledNodes = cfg.nodes.filter((n) => n.enabled)
  const results = await Promise.all(
    enabledNodes.map((node) => rolloutSmokeNode(node, weightsBytes, wver)),
  )

  const selfIdx = enabledNodes.findIndex((n) => n.id === 'self')
  if (selfIdx >= 0 && !results[selfIdx]) {
    return {
      name: 'rollout 冒烟',
      passed: false,
      fatal: true,
      detail: 'self 节点失败——真训练将无法采集',
    }
  }
  if (results.every(Boolean)) return { name: 'rollout 冒烟', passed: true, fatal: true }
  return {
    name: 'rollout 冒烟',
    passed: false,
    fatal: false,
    detail: '部分非 self 节点未通过（降级容忍）',
  }
}

/** self-node 快速可达冒烟（端口 + /v1/ping）。 */
export async function selfNodeSmoke(cfg: RlConfig): Promise<SmokeItem> {
  if (!(await portListen(cfg.rl.agent_port))) {
    return {
      name: 'self-node ping',
      passed: false,
      fatal: false,
      detail: `端口 ${cfg.rl.agent_port} 未监听`,
    }
  }
  const selfKey = cfg.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  const okPing = await httpOk(`http://127.0.0.1:${cfg.rl.agent_port}/v1/ping`, selfKey)
  if (!okPing) {
    return { name: 'self-node ping', passed: false, fatal: false, detail: 'ping 未通过' }
  }
  ok(`self-node ping 通过 (port ${cfg.rl.agent_port})`)
  return { name: 'self-node ping', passed: true, fatal: false }
}

/** 汇总打印 + 硬失败判定。 */
export function summarizeSmoke(items: SmokeItem[]): boolean {
  for (const it of items) {
    if (it.passed) ok(`${it.name}${it.detail ? ` — ${it.detail}` : ''}`)
    else if (it.fatal) fail(`${it.name} — ${it.detail ?? '失败'}`)
    else warn(`${it.name} — ${it.detail ?? '失败（降级容忍）'}`)
  }
  const fatalBad = items.find((i) => !i.passed && i.fatal)
  if (fatalBad) info(`冒烟硬失败: ${fatalBad.name}`)
  return !fatalBad
}
