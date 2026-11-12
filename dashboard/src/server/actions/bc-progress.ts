/** bc-progress.ts — BC 续训进度的人工清空（#8，2026-11-05）。
 *
 * G3 之后重开课程默认从课程级 `(course,it)` 索引接续（40→80 从 ep40 起），这是正确缺省；
 * 但训练方向错了（换标签/换语料前的旧进度）时人需要一个显式出口——否则只能手工删 hub
 * 目录，而 hub 可能在另一台机器上。
 *
 * 纪律：
 *  · 必须先暂停：清空只对未来生效，在跑 job 的下一次 epoch 回传会立刻重建索引。
 *  · 只删 hub 的课程级索引别名：per-jid 权重、epoch-weights/、账本、归档一律不动。
 */

import { loadConfig } from '../../core/config'
import { hubCandidates, hubClearBcResume, hubReleaseBcExclusive } from '../../stack/hub-admin'
import { readPauseFacts } from './loop-control'
import type { ActionResult } from './result'

export async function clearBcResume(course: string): Promise<ActionResult> {
  const c = String(course ?? '').trim()
  if (!c) return { ok: false, message: '缺少 course' }
  if (!readPauseFacts().intent.includes(c)) {
    return {
      ok: false,
      message: `${c} 未暂停——先停课/暂停再清空 BC 进度`,
      detail: [
        '在跑的作业会在下一次 epoch 回传时立刻重建续训索引（清空会被覆盖）。',
        '先停课（或暂停本课），确认没有在飞 job，再点一次。',
      ],
    }
  }

  const cfg = loadConfig()
  const token = String(cfg.rl?.remote_token ?? '')
  let last = '没有可用的 hub 地址'
  for (const base of hubCandidates(cfg, c)) {
    const r = await hubClearBcResume(base, token, c)
    if (!r.reachable) {
      last = r.message
      continue
    }
    if (!r.ok) return { ok: false, message: `清空失败：${r.message}` }
    return {
      ok: true,
      message:
        r.cleared > 0
          ? `已清空 ${c} 的 BC 续训索引（${r.cleared} 轮）——下次开课从 ep1 起`
          : `${c} 没有 BC 续训索引（无需清空）——下次开课本来就从 ep1 起`,
      detail: [
        '只删 hub 的课程级 (course,it) 索引；epoch 权重归档、账本、per-jid 权重不动。',
        '若想连历史一起重来，另开新课程名（§15.5：新 out/traj/backup），不要复用这个名字。',
      ],
    }
  }
  return { ok: false, message: `清空失败：${last}` }
}

/** 2026-11-12：人工解除 BC 课程独占（落盘锁；**不要求**暂停——worker 已死/换机时紧急放行）。
 *
 *  只删 hub 的 `bc-exclusive.json`：在跑的 worker 下一次 epoch 会重建锁（它还在推进），
 *  所以这不会「杀掉」正在训练的 job；它只是让下一份能被别的盘领。
 */
export async function releaseBcExclusive(course: string): Promise<ActionResult> {
  const c = String(course ?? '').trim()
  if (!c) return { ok: false, message: '缺少 course' }
  const cfg = loadConfig()
  const token = String(cfg.rl?.remote_token ?? '')
  let last = '没有可用的 hub 地址'
  for (const base of hubCandidates(cfg, c)) {
    const r = await hubReleaseBcExclusive(base, token, c)
    if (!r.reachable) {
      last = r.message
      continue
    }
    if (!r.ok) return { ok: false, message: `解除 BC 独占失败：${r.message}` }
    return {
      ok: true,
      message: r.cleared
        ? `已解除 ${c} 的 BC 独占（原 worker=${r.worker_id || '?'} job=${r.job_id || '?'}）——下一份可被其它盘领取`
        : `${c} 当前没有 BC 独占（无需解除）`,
      detail: [
        '只删 hub 的 bc-exclusive.json；租约、进度、权重不动。',
        '若原 worker 仍在跑，它下一次 epoch 会重建锁——那就等于没解除，先确认它已停止。',
      ],
    }
  }
  return { ok: false, message: `解除 BC 独占失败：${last}` }
}
