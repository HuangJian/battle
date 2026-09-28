/**
 * server-metrics-harvest.test.ts — 逐轮实际值的**抗轮转抄录**：已开课课程的
 * 「耗时/击杀/残血/道具/承伤·杀」不依赖「谁在控制台上看着」。
 *
 * 分层：src/server/api/snapshot-refresher.ts（`harvestTrainingCourseActuals` / `startSnapshotRefresher`）
 *
 * 为什么必须单独钉住（2026-09-28 事故）：这几列是**扫盘现算**的派生值——
 * `iters.ts::readIterMetrics` 扫 `it<N>` 下的单局 `manifest.json`（云机腿 `it<N>/per-game.json`），
 * 算一次落 `<traj>/.pool-actuals-cache.json`。而 `/api/state` 只对**查看课程**算它，
 * 训练侧又每轮按 `keep_iters`（rl-config 缺省 3）删 `it<N>` ⇒ 没被看过的那门课的轮次
 * 到第 4 轮就永久空白（实测 h5b-clean 缺 it7–17、h5a-earlydmg 缺 it22–25，两个缺口
 * 互补 = 「谁在屏幕上谁才有数」）。本用例把「未被查看的已开课课程也活下来」钉死：
 * 抄录一轮 → 删掉 `it<N>`（模拟轮转）→ 表上四列仍在。
 */

import os from 'os'
import path from 'path'
import { afterAll, describe, expect, it } from 'bun:test'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'

// env 必须在被测模块 import 之前（`core/paths.ts` 是惰性取值，但顺序写对才不踩雷）。
const tmpRoot = mkdtempSync(path.join(os.tmpdir(), 'bcity-harvest-'))
const curriculaRoot = mkdtempSync(path.join(os.tmpdir(), 'bcity-harvest-cur-'))
process.env.BCITY_TMP_LOGS_DIR = tmpRoot
process.env.BCITY_CURRICULA_DIR = curriculaRoot
process.env.BCITY_ARCHIVE_DIR = path.join(tmpRoot, '__no-archive__')

const { harvestTrainingCourseActuals } = await import('../src/server/api/snapshot-refresher')
const { readIterMetrics } = await import('../src/server/iters')

afterAll(() => {
  rmSync(tmpRoot, { recursive: true, force: true })
  rmSync(curriculaRoot, { recursive: true, force: true })
})

/** 一局压缩画像（字段名与单局 manifest 逐字相同，见 `rl/reports.py::_PER_GAME_FIELDS`）。 */
function game(stage: number, seed: number) {
  return {
    stage,
    seed,
    nSamples: 100,
    kills: 6,
    ticks: 2000,
    outcome: 'stage_clear',
    powerUpsCollected: 1,
    playerDamageTaken: 300,
    playerDeaths: 0,
    puGotTank: 1,
    startLives: 1,
    enemyTotal: 20,
    puSpawnBomb: 1,
  }
}

/** 造一门课：账本一行 it1 + it1/per-game.json（`enabled` ⇒ 写开课标记，= 「在训」判据）。 */
function seedCourse(name: string, enabled: boolean): string {
  const dir = path.join(tmpRoot, name)
  mkdirSync(path.join(dir, 'it1'), { recursive: true })
  writeFileSync(
    path.join(dir, 'training_log.jsonl'),
    JSON.stringify({ event: 'iteration', iter: 1, time: 't-1', winRate: 0.5 }) +
      String.fromCharCode(10),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'it1', 'per-game.json'),
    JSON.stringify([game(2000, 1), game(2000, 2)]),
    'utf-8',
  )
  if (enabled) writeFileSync(path.join(dir, 'training-enabled.txt'), '', 'utf-8')
  return dir
}

function readCache(dir: string): Record<string, { totalKills: number; games: number }> {
  return JSON.parse(readFileSync(path.join(dir, '.pool-actuals-cache.json'), 'utf-8')) as Record<
    string,
    { totalKills: number; games: number }
  >
}

describe('console/metrics-harvest 逐轮实际值抗轮转抄录', () => {
  it('未被查看的已开课课程也抄录；it<N> 被轮转删掉后四列仍在', () => {
    const a = seedCourse('harvest-a', true)
    const b = seedCourse('harvest-b', true)

    const harvested = harvestTrainingCourseActuals()
    expect(harvested).toContain('harvest-a')
    expect(harvested).toContain('harvest-b')
    // 两门课都落了盘上缓冲——其中 harvest-b 从未走过请求路径（没人查看它）。
    expect(readCache(a)['1']!.totalKills).toBe(12)
    expect(readCache(b)['1']!.totalKills).toBe(12)

    // 模拟训练侧 `_rotate_cleanup`：旧轮目录被删。
    rmSync(path.join(b, 'it1'), { recursive: true, force: true })

    // 「打开控制台切到 harvest-b」= 请求路径现算：此刻盘上已经没有 it1 了，
    // 四列仍必须来自抄录，而不是空白。
    const { rows } = readIterMetrics(b)
    expect(rows.length).toBe(1)
    expect(rows[0]!.actuals).not.toBeNull()
    expect(rows[0]!.actuals!.totalKills).toBe(12)
    expect(rows[0]!.actuals!.avgTicks).toBe(2000)
  })

  it('未开课课程不抄录（几十门历史课表不被逐拍扫盘）', () => {
    const c = seedCourse('harvest-c', false)
    const harvested = harvestTrainingCourseActuals()
    expect(harvested).not.toContain('harvest-c')
    expect(existsSync(path.join(c, '.pool-actuals-cache.json'))).toBe(false)
    // 查看它时（请求路径）照常现算——只是不占后台抄录预算。
    const { rows } = readIterMetrics(c)
    expect(rows[0]!.actuals!.totalKills).toBe(12)
  })

  it('重复抄录幂等：账本没动 ⇒ 第二轮零抄录、缓存逐字节不变（稳态零成本）', () => {
    const d = seedCourse('harvest-d', true)
    expect(harvestTrainingCourseActuals()).toContain('harvest-d')
    const cachePath = path.join(d, '.pool-actuals-cache.json')
    const first = readFileSync(cachePath, 'utf-8')
    expect(harvestTrainingCourseActuals()).toEqual([]) // 签名闸：账本未变 ⇒ 不抄
    expect(readFileSync(cachePath, 'utf-8')).toBe(first)
  })

  it('账本行先到、画像后到 ⇒ 下一拍必须重试（缺口不被签名闸自锁）', () => {
    const e = seedCourse('harvest-e', true)
    // 模拟「账本行已写、`it<N>` 下的画像还没落」：这一拍抄不到实际值。
    rmSync(path.join(e, 'it1', 'per-game.json'), { force: true })
    expect(harvestTrainingCourseActuals()).toContain('harvest-e')
    expect(existsSync(path.join(e, '.pool-actuals-cache.json'))).toBe(false)

    // 画像随后落地（**账本行没变**）：必须再抄一次才算数——否则就是空白。
    writeFileSync(path.join(e, 'it1', 'per-game.json'), JSON.stringify([game(2000, 7)]), 'utf-8')
    expect(harvestTrainingCourseActuals()).toContain('harvest-e')
    expect(readCache(e)['1']!.totalKills).toBe(6)
  })
})
