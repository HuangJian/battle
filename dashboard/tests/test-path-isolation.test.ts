/**
 * test-path-isolation.test.ts — **零测试读/写真实工作目录**的结构性守卫（用户 2026-10-19 定调）。
 *
 * ## 为什么是"守卫"而不是"又一条用例"
 *
 * 在此之前，`core/paths.ts` 里每一个惰性 env 重定向都是**自愿参加**的：测试自己记得设才隔离。
 * 实测代价：`dashboard/data/course-metrics/`（热备份库，20 门课）被 `pool-history` 的
 * `addHotMirrorCandidates()` 当成课程候选读进聚合 ⇒ 池历史/贡献数用例在真实库存在时随机红
 * （`y23-acc-off` 这种真课名直接出现在断言里）；`auto-offline-handoff` 更是把 `__ahand-*__`
 * 假课**写**进仓根 `tmp/`。
 *
 * 自愿参加 = 必然漏 ⇒ 提升为 `tests/helpers/isolate-paths.ts` 预载（进程级，早于任何测试模块
 * 的 import）。本文件钉三件事，任一失效都会当场红，而不是等某条用例随机红：
 *
 *   ① **运行时路径全部落在隔离根内**（且不在仓根 tmp / dashboard data / nn-training 之下）；
 *   ② **生产代码不再硬编码 `REPO_ROOT/tmp`**（源码级扫描；新写一处即红）；
 *   ③ **预载确实挂着**（`bun run test` 之外裸跑 `bun test <file>` 会在①上红）。
 *
 * 边界：`curriculaDir()` **刻意不隔离** —— `nn-training/curricula/` 是入库源数据（与 `src/`
 * 同级的只读事实源），重定向它会让"读课程配置"的用例整体失去对象。它与 `tmp/` 这类**运行时**
 * 工作目录不是一类；要动它得单独一刀（连同所有依赖它的用例一起搬进夹具）。
 */

import { describe, expect, it } from 'bun:test'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import {
  DASHBOARD_ROOT,
  LOG_DIR,
  REPO_ROOT,
  configPath,
  courseMetricsDir,
  curriculaDir,
  errorLedgerPath,
  loopControlPath,
  tmpCourseDir,
  tmpLogsDir,
  tmpPoolDir,
} from '../src/core/paths'
import { ISOLATED_ROOT } from './helpers/isolate-paths'

describe('测试路径隔离（helpers/isolate-paths.ts 预载）', () => {
  it('隔离根本身在 OS 临时目录里，不在仓库内', () => {
    expect(existsSync(ISOLATED_ROOT)).toBe(true)
    expect(ISOLATED_ROOT.startsWith(REPO_ROOT)).toBe(false)
    expect(ISOLATED_ROOT.startsWith(DASHBOARD_ROOT)).toBe(false)
  })

  it('运行时路径全部落在隔离根内（一个都不许落到真实工作目录）', () => {
    const resolved = {
      configPath: configPath(),
      tmpLogsDir: tmpLogsDir(),
      tmpPoolDir: tmpPoolDir(),
      tmpCourseDir: tmpCourseDir('any-course'),
      errorLedgerPath: errorLedgerPath(),
      loopControlPath: loopControlPath(),
      courseMetricsDir: courseMetricsDir(),
    }
    for (const [name, p] of Object.entries(resolved)) {
      expect(p.startsWith(ISOLATED_ROOT), `${name} → ${p}`).toBe(true)
      // 双向：也不在仓根 tmp / dashboard data / nn-training 之下（那些都是真实工作目录）
      expect(p.startsWith(LOG_DIR), `${name} 落在仓根 tmp`).toBe(false)
      expect(p.startsWith(path.join(DASHBOARD_ROOT, 'data')), `${name} 落在 dashboard/data`).toBe(
        false,
      )
      expect(p.startsWith(path.join(REPO_ROOT, 'nn-training')), `${name} 落在 nn-training`).toBe(
        false,
      )
    }
  })

  it('预载自带的种子 rl-config 存在且是最小夹具（无节点、无课程）', () => {
    const cfg = JSON.parse(readFileSync(configPath(), 'utf-8')) as {
      version: number
      nodes: unknown[]
      courses?: unknown
    }
    expect(cfg.version).toBe(1)
    expect(cfg.nodes).toEqual([])
    expect(cfg.courses).toBeUndefined()
  })

  it('备份库是空的（真实那 20 门课不许被读进来）', () => {
    let names: string[] = []
    try {
      names = readdirSync(courseMetricsDir())
    } catch {
      names = [] // 目录不存在也是"空"
    }
    expect(names.filter((n) => !n.startsWith('.'))).toEqual([])
  })

  it('curriculaDir 刻意**不**隔离（入库源数据，与 src/ 同级）', () => {
    // 这条是上面边界的反面证据：哪天有人把它也加进预载，这里会红，逼他先想清楚。
    expect(curriculaDir().startsWith(REPO_ROOT)).toBe(true)
  })
})

describe('生产代码不许再硬编码 REPO_ROOT/tmp（源码级围栏）', () => {
  it('src 下除 core/paths.ts 的定义外，零处硬编码的 REPO_ROOT/tmp', () => {
    const offenders: string[] = []
    const walk = (dir: string): void => {
      for (const ent of readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, ent.name)
        if (ent.isDirectory()) {
          if (ent.name === 'node_modules' || ent.name === '.build') continue
          walk(p)
        } else if (ent.name.endsWith('.ts')) {
          if (p === path.join(DASHBOARD_ROOT, 'src', 'core', 'paths.ts')) continue
          const txt = readFileSync(p, 'utf-8')
          if (txt.includes("REPO_ROOT, 'tmp'")) offenders.push(p)
        }
      }
    }
    walk(path.join(DASHBOARD_ROOT, 'src'))
    expect(offenders).toEqual([])
  })
})
