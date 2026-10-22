/**
 * web-view-format.test.ts — 时间 / 文本格式化纯函数：fmtTs 同日跨日、fmtFullTs、fmtPct、stripIsoPrefix、formatBytes
 *
 * 分层：src/web/view/format.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import {
  fmtFullTs,
  fmtNum,
  fmtPct,
  fmtPhaseSecs,
  fmtTs,
  phaseSecs,
  phaseSecsTitle,
  stripIsoPrefix,
} from '../src/web/view'

// ────────────────────────── 纯函数：时间 / 文本 ──────────────────────────
describe('view 纯函数：时间与文本', () => {
  it('fmtTs：同日省略日期 HH:mm:ss；跨日 MM-DD HH:mm:ss（GLM-U3 精确化）', () => {
    const now = new Date(2026, 8, 7, 12, 0, 0).getTime()
    expect(fmtTs(new Date(2026, 8, 7, 9, 8, 5).getTime(), now)).toBe('09:08:05')
    expect(fmtTs(new Date(2026, 8, 6, 23, 59, 59).getTime(), now)).toBe('09-06 23:59:59')
    expect(fmtTs(new Date(2026, 7, 7, 9, 8, 5).getTime(), now)).toBe('08-07 09:08:05')
  })

  it('fmtFullTs：完整「YYYY-MM-DD HH:MM:SS」（历史锚点比较用）', () => {
    expect(fmtFullTs(new Date(2026, 8, 7, 9, 8, 5).getTime())).toBe('2026-09-07 09:08:05')
  })

  it('fmtPct / stripIsoPrefix', () => {
    expect(fmtPct(0.123)).toBe('12.3%')
    expect(fmtPct(null)).toBe('—')
    expect(stripIsoPrefix('2026-09-07T02:03:04.567Z link timeout')).toBe('link timeout')
    expect(stripIsoPrefix('2026-09-07T02:03:04Z s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('2026-09-07T02:03:04 s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('plain error')).toBe('plain error')
  })
})

describe('phaseSecs：rollout/ppo/net 准确拆分', () => {
  it('远端 pull：纯采集 + 真训练 + 网络（下发 + 往返超出）', () => {
    const p = phaseSecs({
      rolloutSec: 400,
      ppoSec: 120,
      pureCollectSec: 350,
      ppoCloudSec: 80,
      distPhaseSec: 20,
    })
    expect(p).toEqual({ rollout: 350, ppo: 80, net: 60 }) // 20 dist + 40 (120-80)
    expect(fmtPhaseSecs(p)).toBe('350/80/60s')
    expect(phaseSecsTitle(p)).toContain('权重分发')
  })

  it('本机/流式：无 cloud 拆分 → net 仅 dist；ppo 回退 ppoSec', () => {
    expect(
      phaseSecs({
        rolloutSec: 200,
        ppoSec: 90,
        pureCollectSec: null,
        ppoCloudSec: null,
        distPhaseSec: 5,
      }),
    ).toEqual({ rollout: 200, ppo: 90, net: 5 })
  })

  it('旧账本（全缺）→ 回退 rollout/ppo，net=0', () => {
    expect(
      phaseSecs({
        rolloutSec: 60,
        ppoSec: 40,
        pureCollectSec: null,
        ppoCloudSec: null,
        distPhaseSec: null,
      }),
    ).toEqual({ rollout: 60, ppo: 40, net: 0 })
  })

  // ★ 2026-10-19 事故回归：`rollout_end` 行的 PPO 侧字段在服务端写 NaN，而
  //   **JSON.stringify 把 NaN 序列化成 null** ⇒ 客户端拿到的是 null。旧 phaseSecs 的
  //   `r.ppoCloudSec != null ? … : r.ppoSec` 两条都是 null ⇒ `null.toFixed(0)` 抛，
  //   整个 Hero 主表掉进 PanelErrorBoundary（用户报障「该卡片加载失败」）。
  //   it0 基线行一向是同形状的 null，但它被 heroMainRows 的 iter>0 挡在主表外，
  //   所以这个坑一直到 pending 行（iter>0）出现才炸。
  it('rollout_end 行（PPO 未回传，过 JSON 后是 null）⇒ ppo/net 为 null 且不抛', () => {
    const p = phaseSecs({
      rolloutSec: 45.3,
      ppoSec: null,
      pureCollectSec: 41.9,
      ppoCloudSec: null,
      distPhaseSec: null,
    })
    expect(p).toEqual({ rollout: 41.9, ppo: null, net: null })
    // 渲染必须显「—」，不是 0（0 会被读成「训练用了 0 秒」）也不是 NaN
    expect(fmtPhaseSecs(p)).toBe('42/—/—s')
    expect(phaseSecsTitle(p)).toContain('ppo 真训练 —s')
  })

  it(' rolloutSec 也缺（极端）⇒ rollout 回落 0，仍不抛', () => {
    const p = phaseSecs({ rolloutSec: null, ppoSec: null })
    expect(p).toEqual({ rollout: 0, ppo: null, net: null })
    expect(fmtPhaseSecs(p)).toBe('0/—/—s')
  })
})

describe('fmtNum：无读数显 —（JSON 的 null / NaN 都不画 0）', () => {
  it('null / undefined / NaN → —；正常值按位数', () => {
    expect(fmtNum(null, 4)).toBe('—')
    expect(fmtNum(undefined, 4)).toBe('—')
    expect(fmtNum(Number.NaN, 4)).toBe('—')
    expect(fmtNum(0, 4)).toBe('0.0000')
    expect(fmtNum(0.018562, 4)).toBe('0.0186')
    expect(fmtNum(0.00015, 6)).toBe('0.000150')
  })

  it('fmtPct 同口径（无读数 → —，不画 0.0%）', () => {
    expect(fmtPct(null)).toBe('—')
    expect(fmtPct(Number.NaN)).toBe('—')
    expect(fmtPct(0.5849)).toBe('58.5%')
  })
})
