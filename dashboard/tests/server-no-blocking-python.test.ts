/**
 * server-no-blocking-python.test.ts — 控制台服务端**不许再出现同步子进程**（2026-09-22）
 *
 * 分层：dashboard/src/server/**（HTTP 请求路径 + 后台刷新器都在一个事件循环上）
 *
 * 为什么这条是硬规则：`spawnSync` / `execSync` 把**整个事件循环**按住。控制台服务端只有
 * 一个线程——按住它期间，`/api/state`、5s 慢快照刷新器、局域网其它查看者的读取全部排队：
 *
 *  · 2026-09-22 事故①（用户报障）：产物导入用 `spawnSync` 跑 python 导入器（上限 300s），
 *    并 `writeFileSync` 落最多 512MB 的上传体 —— 导入那十几秒里「整个控制台卡住」；
 *    同一天它还让 SWR 的「重算丢后台」名存实亡（旧值响应要等 spawnSync 返回才发得出去）。
 *  · 同一族：只读调度器视图（python 子进程）当缓存重算体时必须走异步孪生。
 *
 * 修法：`spawnRunPython`（detach，长任务）与 `runRunPythonAsyncModule/Script`（异步捕获，
 * 要结果的短任务）两种模式，**没有第三种**；同步变体已从 `run-python.ts` 删除。
 *
 * 本用例是那条规则的守门人：扫 `src/server/**` 的**调用**（`spawnSync(` / `execSync(`），
 * 注释里提到名字不算（规则本身就在这里写了好几次）。`src/core|launch|evalboard` 不在范围内
 * ——那里是启动器/CLI（进程自己就是终点，没有并发读要在乎）。
 */

import { describe, expect, it } from 'bun:test'
import { readdirSync, readFileSync } from 'fs'
import { join } from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'

function walk(dir: string, out: string[]): string[] {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else if (ent.name.endsWith('.ts')) out.push(p)
  }
  return out
}

describe('服务端不许同步子进程（事件循环是共享资源）', () => {
  it('src/server/** 无 spawnSync/execSync 调用（要结果就走 runRunPythonAsync*）', () => {
    const files = walk(join(DASHBOARD_ROOT, 'src', 'server'), [])
    expect(files.length).toBeGreaterThan(20) // 扫描面没空（路径搬迁后这个用例要立刻红）
    const offenders = files.filter((f) =>
      /\b(spawnSync|execSync)\s*\(/.test(readFileSync(f, 'utf8')),
    )
    expect(offenders.map((f) => f.slice(DASHBOARD_ROOT.length + 1))).toEqual([])
  })

  it('python 一次性脚本两条路都在（detach / 异步捕获），且没有同步孪生', () => {
    const src = readFileSync(join(DASHBOARD_ROOT, 'src', 'server', 'run-python.ts'), 'utf8')
    expect(src).toContain('export function spawnRunPython')
    expect(src).toContain('export function runRunPythonAsyncModule')
    expect(src).toContain('export function runRunPythonAsyncScript')
    // 同步变体不得回来（回来了就会被用来跑导入器 —— 那就是事故①）
    expect(src).not.toMatch(/export function runRunPythonSync/)
  })
})
