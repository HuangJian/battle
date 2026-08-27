/**
 * test-dist-ops.ts — 测试 rollout agent 运维功能（/v1/update、/v1/restart）与连通性。
 *
 * 用法：bun tools/agent/test-dist-ops.ts [--node mac] [--branch intent-ai]
 *   --pull  执行一次 /v1/update（git pull 指定 branch）
 *   --restart 执行一次 /v1/restart（重启 agent，应用最新代码）
 * 缺省只 ping 各节点报告存活/版本。
 */
import { readFileSync } from 'node:fs'

const arg = (n: string, fb?: string) => {
  const i = process.argv.indexOf(`--${n}`)
  return i >= 0 ? process.argv[i + 1] : fb
}
const only = arg('node', 'mac')
const doPull = process.argv.includes('--pull')
const doRestart = process.argv.includes('--restart')
const branch = arg('branch', '')

const cfg = JSON.parse(readFileSync('nn-training/dist-nodes.json', 'utf8')) as {
  nodes: Array<{
    id: string
    url: string
    authKey: string
    concurrency?: number
    enabled?: boolean
  }>
}

async function ping(n: {
  id: string
  url: string
  authKey: string
}): Promise<Record<string, unknown> | null> {
  try {
    const r = await fetch(`${n.url}/v1/ping`, {
      headers: { Authorization: `Bearer ${n.authKey}` },
      signal: AbortSignal.timeout(5000),
    })
    if (r.status !== 200) return { http: r.status } as Record<string, unknown>
    return (await r.json()) as Record<string, unknown>
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) } as Record<string, unknown>
  }
}

const targets = cfg.nodes.filter((n) => only === 'all' || n.id === only)
for (const n of targets) {
  console.log(`=== node ${n.id} (${n.url}) ===`)
  const p0 = await ping(n)
  console.log('ping:', JSON.stringify(p0))
  if (doPull) {
    try {
      const r = await fetch(`${n.url}/v1/update`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${n.authKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch: branch || undefined }),
        signal: AbortSignal.timeout(150_000),
      })
      console.log('update:', r.status, await r.text())
    } catch (e) {
      console.log('update error:', e instanceof Error ? e.message : String(e))
    }
  }
  if (doRestart) {
    try {
      const r = await fetch(`${n.url}/v1/restart`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${n.authKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ pullBranch: branch || undefined, delayMs: 800 }),
        signal: AbortSignal.timeout(30_000),
      })
      console.log('restart:', r.status, await r.text())
    } catch (e) {
      console.log('restart error:', e instanceof Error ? e.message : String(e))
    }
    await new Promise((res) => setTimeout(res, 6000))
    const p1 = await ping(n)
    console.log('re-ping after restart:', JSON.stringify(p1))
  }
}
