/**
 * cloud-halt-banner.test.ts — 首页云端停机横幅的**可见性**纯函数（2026-09-14 回归）。
 *
 * 事故：切到 `bc-c4-v3` 后，首页仍常驻 c6-chip 的「⚠ 课程 c6-chip 停机中」红横幅且
 * 关不掉。两个原因叠加：
 *  1. 横幅直接遍历全量 `cloudHalts`（与当前课程无关）；
 *  2. 文案承诺的「停机条件消失（恢复训练）自动解除」只对本课成立——自动解除发生在
 *     `startComponent(trainingLoop)` 里对本课程调用 markCloudHaltRecovered。
 *     ⇒ 在别的课上训练，永远解不掉别课的横幅；唯一的出口「立即恢复」只作用于那门课。
 *
 * 修复：首页横幅只弹**当前视图课程**的停机记录（visibleCloudHalts），其它课程由
 * 多课总览徽标承载；并给红横幅补「知道了」（按事件身份 ack，新一次停机重新弹）。
 */

import { describe, expect, it } from 'bun:test'
import {
  cloudHaltAckKey,
  parseCloudHaltAcks,
  visibleCloudHalts,
  type CloudHaltView,
} from '../src/web/view'

const halted = (at: string, reason = 'r'): CloudHaltView => ({ at, reason, status: 'halted' })
const recovered = (at: string, clearedAt: string): CloudHaltView => ({
  at,
  reason: 'r',
  status: 'recovered',
  clearedAt,
})

describe('visibleCloudHalts：首页横幅只弹当前课程', () => {
  it('当前课程有停机记录 → 只返回它（别课的停机不再霸屏）', () => {
    const halts = { 'c6-chip': halted('T1'), 'bc-c4-v3': halted('T2') }
    expect(visibleCloudHalts(halts, 'bc-c4-v3')).toEqual([['bc-c4-v3', halts['bc-c4-v3']]])
  })

  it('切回该课程 → 横幅重新出现（过滤不是删除）', () => {
    const halts = { 'c6-chip': halted('T1') }
    expect(visibleCloudHalts(halts, 'c6-chip')).toEqual([['c6-chip', halts['c6-chip']]])
  })

  it('当前课程无记录 → 回退旧无课程键 ""（旧账本兼容）', () => {
    const legacy = halted('T0')
    expect(visibleCloudHalts({ '': legacy }, 'bc-c4-v3')).toEqual([['', legacy]])
  })

  it('无记录 / 空表 / 未传 → 空（横幅不出现）', () => {
    expect(visibleCloudHalts({}, 'bc-c4-v3')).toEqual([])
    expect(visibleCloudHalts(undefined, 'bc-c4-v3')).toEqual([])
    expect(visibleCloudHalts({ 'c6-chip': halted('T1') }, 'bc-c4-v3').length).toBe(0)
  })

  it('recovered（灰横幅）同样只弹当前课的历史', () => {
    const halts = { 'c6-chip': recovered('T1', 'T2'), 'bc-c4-v3': recovered('T3', 'T4') }
    expect(visibleCloudHalts(halts, 'bc-c4-v3')).toEqual([['bc-c4-v3', halts['bc-c4-v3']]])
  })
})

describe('cloudHaltAckKey：按事件身份 ack（新事件重新弹）', () => {
  it('课程 + 时刻构成身份；新一次停机 → 新 key（不会被旧 ack 吃掉）', () => {
    const a = cloudHaltAckKey('halted', 'c6-chip', 'T1')
    const b = cloudHaltAckKey('halted', 'c6-chip', 'T2')
    expect(a).not.toBe(b)
    expect(cloudHaltAckKey('halted', 'c6-chip', 'T1')).toBe(a)
  })

  it('halted 与 recovered 不互串（同一时刻两种横幅各自 ack）', () => {
    expect(cloudHaltAckKey('halted', 'c', 'T')).not.toBe(cloudHaltAckKey('recovered', 'c', 'T'))
  })
})

describe('parseCloudHaltAcks：localStorage 格式兼容', () => {
  it('新格式（JSON 数组）', () => {
    expect(parseCloudHaltAcks('["halted|c|T1"]')).toEqual(['halted|c|T1'])
  })

  it('旧格式（单值字符串）→ 视为单元素，不丢已读', () => {
    expect(parseCloudHaltAcks('c6-chip|T2')).toEqual(['c6-chip|T2'])
  })

  it('空 / 非法 → 空数组', () => {
    expect(parseCloudHaltAcks(null)).toEqual([])
    expect(parseCloudHaltAcks('')).toEqual([])
    expect(parseCloudHaltAcks('not json')).toEqual(['not json']) // 无法解析即按旧格式处理
  })
})
