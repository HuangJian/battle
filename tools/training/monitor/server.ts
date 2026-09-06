/** server.ts — /pool 节点池监控独立服务（Bun.serve，替代从 sampler-agent 访问）。

 *  用途：任意机器 `bun tools/training/monitor/server.ts --port 8901` 起一个只读
 *  监控页；主控机（rl-config 有 nodes）渲染全表，非主控机渲染"无节点配置"占位。
 *  页面模块 mtime 键控动态 import 热加载（DECISIONS §341 语义延续）：改 page.ts
 *  及其依赖即时生效，无需重启服务；坏文件沿用上一版可用模块。
 *
 *  sampler-agent 的 GET /pool 仍可用（agent 侧接线见 tools/agent/sampler-agent.ts
 *  的 loadPoolPage——目标文件迁移为本目录 page.ts 的等价导出）。
 */

import { statSync, readFileSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, NN_TRAINING } from '../paths'
import type { NodeConf } from '../types'

const PAGE_TS = path.join(import.meta.dir, 'page.ts')

interface ServeOpts {
  port: number
}

function parseArgs(): ServeOpts {
  let port = 8901
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--port') port = parseInt(argv[++i] ?? '', 10) || 8901
  }
  return { port }
}

function loadPoolConfig(): { nodes: NodeConf[] | null; localSlots: number | null } {
  try {
    const cfg = JSON.parse(readFileSync(CONFIG_PATH, 'utf8')) as {
      nodes?: Array<{
        id?: string
        url: string
        authKey?: string
        enabled?: boolean
        concurrency?: number
        gpu_push?: boolean
      }>
      rl?: { local_slots?: number }
    }
    const nodes: NodeConf[] = (cfg.nodes ?? [])
      .filter((n) => n && typeof n.url === 'string')
      .map((n) => ({
        id: n.id || n.url,
        url: n.url,
        authKey: n.authKey ?? '',
        enabled: n.enabled !== false,
        concurrency: n.concurrency ?? 1,
        ...(n.gpu_push ? { gpu_push: true } : {}),
      }))
    const v = cfg.rl?.local_slots
    return {
      nodes: nodes.length > 0 ? nodes : null,
      localSlots: typeof v === 'number' && Number.isInteger(v) && v > 0 ? v : null,
    }
  } catch {
    return { nodes: null, localSlots: null }
  }
}

// 本机 codeHash 惰性 memo（页面只需"节点版本是否与本机一致"这一判定）。
let hashMemo: string | null = null
let hashAt = 0
function localCodeHash(): string {
  // 5s TTL 重算——codehash 集文件大，页面刷新频繁不宜每次全量哈希。
  if (hashMemo && Date.now() - hashAt < 5000) return hashMemo
  try {
    const { collectCodeHashEntries, computeCodeHashFromFiles } =
      require('../../agent/sampler-agent') as {
        collectCodeHashEntries: () => { relPath: string; content: Buffer }[]
        computeCodeHashFromFiles: (e: { relPath: string; content: Buffer }[]) => string
      }
    hashMemo = computeCodeHashFromFiles(collectCodeHashEntries())
  } catch {
    hashMemo = ''
  }
  hashAt = Date.now()
  return hashMemo
}

// page.ts 热加载（mtime 键控动态 import，§341 语义）。
let pageMod: { mtimeMs: number; mod: typeof import('./page') } | null = null
async function loadPage(): Promise<typeof import('./page') | null> {
  try {
    const mtimeMs = statSync(PAGE_TS).mtimeMs
    if (pageMod && pageMod.mtimeMs === mtimeMs) return pageMod.mod
    const mod = await import(`./page.ts?m=${mtimeMs}`)
    pageMod = { mtimeMs, mod }
    console.log(`[monitor] page.ts hot-reloaded (mtime=${Math.round(mtimeMs)})`)
    return mod
  } catch (e) {
    console.error(
      `[monitor] page.ts hot-reload FAILED — serving previous version: ${String(e).slice(0, 160)}`,
    )
    return pageMod?.mod ?? null
  }
}

async function main(): Promise<void> {
  const { port } = parseArgs()
  const server = Bun.serve({
    port,
    async fetch(req) {
      const url = new URL(req.url)
      if (url.pathname !== '/' && url.pathname !== '/pool') {
        return new Response('not found', { status: 404 })
      }
      const page = await loadPage()
      if (!page) {
        return new Response(
          '<html><body><p>monitor/page.ts unavailable — see log</p></body></html>',
          {
            status: 500,
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
          },
        )
      }
      const { nodes, localSlots } = loadPoolConfig()
      const html = await page.renderMonitorPage({
        workers: 0,
        inflight: { size: 0 },
        gamesDoneTotal: 0,
        localHash: localCodeHash,
        nodes,
        localSlots,
      })
      return new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })
    },
  })
  console.log(
    `[monitor] node-pool page on http://127.0.0.1:${server.port}/pool  (nn-training: ${NN_TRAINING})`,
  )
  console.log('[monitor] stop: Ctrl-C')
}

void main()
