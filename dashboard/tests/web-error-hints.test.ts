/**
 * web-error-hints.test.ts — 错误分类的**展示判据**（`/errors` 页 + 告警坞共用）。
 *
 * 用户口径（2026-10-20）：「cloudflared 类网络抖动错误，不需要运维处理，能不能在页面上用
 * 不同样式显示？需要在首页横幅告警吗？」——三段答案各由一条纯函数承担：
 *   ① 首页横幅：`isNoiseClassification` 把噪声排除在计数/ack 之外（`alerts.ts`，已有用例）；
 *   ② 页面样式：`errorStatusLabel` / `errorGroupNote` 让噪声行显式写「自动撤销 / 无需处置」；
 *   ③ 展开提示：`classificationHint` 对噪声两类给「无需人工处置 + 多久自愈」。
 *
 * 为什么钉纯函数而不是 SSR：噪声行默认折叠（`showNoise=false`），首帧根本渲不到它们；
 * 而这三个判据正是客户端切到「显示噪声」后上屏的全部内容。
 */
import { describe, expect, it } from 'bun:test'
import {
  ERROR_CLASSIFICATION_LABEL,
  classificationHint,
  errorGroupNote,
  errorStatusLabel,
  isNoiseClassification,
} from '../src/web/view'

describe('噪声分类的展示判据（transient-network / process-lifecycle）', () => {
  it('只有网络抖动与进程生命周期算噪声；其余（含 unknown）都要人看', () => {
    expect(isNoiseClassification('transient-network')).toBe(true)
    expect(isNoiseClassification('process-lifecycle')).toBe(true)
    for (const c of [
      'dependency',
      'multi-process',
      'capacity',
      'remote-failure',
      'unknown',
    ] as const) {
      expect(isNoiseClassification(c)).toBe(false)
    }
  })

  it('状态文案：噪声 = 自动撤销（不是待办）；已确认优先；真问题仍是未处理', () => {
    expect(errorStatusLabel('transient-network', false)).toBe('自动撤销')
    expect(errorStatusLabel('process-lifecycle', false)).toBe('自动撤销')
    expect(errorStatusLabel('transient-network', true)).toBe('已确认')
    expect(errorStatusLabel('unknown', false)).toBe('未处理')
    expect(errorStatusLabel('dependency', true)).toBe('已确认')
  })

  it('分组副注：噪声组明说「无需处置 + 两拍自愈」；真问题组不占位', () => {
    expect(errorGroupNote('transient-network')).toContain('无需处置')
    expect(errorGroupNote('transient-network')).toContain('连续两拍')
    expect(errorGroupNote('process-lifecycle')).toContain('无需处置')
    expect(errorGroupNote('unknown')).toBe('')
    expect(errorGroupNote('capacity')).toBe('')
  })

  it('展开提示：噪声两类都给「无需人工处置」及自愈口径，不留空白', () => {
    const net = classificationHint('transient-network')
    expect(net).toContain('无需人工处置')
    expect(net).toContain('自动撤销')
    expect(net).toContain('cloudflared')
    const life = classificationHint('process-lifecycle')
    expect(life).toContain('无需人工处置')
    expect(life).toContain('自动撤销')
    // 真问题仍给动作（不被噪声文案吞掉）
    expect(classificationHint('dependency')).toContain('安装缺失依赖')
  })

  it('分类标签仍在（灰行不丢信息：这是哪一类错、严重度列照旧）', () => {
    expect(ERROR_CLASSIFICATION_LABEL['transient-network']).toBe('网络抖动')
    expect(ERROR_CLASSIFICATION_LABEL['process-lifecycle']).toBe('进程生命周期')
  })
})
