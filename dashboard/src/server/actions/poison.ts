/** poison.ts — 毒包熔断的**人工解冻**（plan/accident.plan.md §4.1 的控制台面，2026-09-21）。
 *
 *  背景：hub 侧对「认领后零回传」达 3 次（`FREEZE_AFTER_RECLAIMS`）的 job 落冻并移出可领池
 *  ——防的是 C-0 it58 那种**内容决定性毒包**（同一份 payload 每次都在同一处炸，3.5h × 40 次
 *  零告警）。冻结是**停机**，不是死刑：这份 job 的字节可能已经修好（重发新包 = 新 job_id），
 *  也可能操作员确认无碍要放回池子——`POST /admin/unfreeze` 就是那个口。
 *
 *  为什么解冻要在控制台而不是让人 curl：
 *    · 它需要一个**人类判断**（为什么它被冻）、一个**可追溯**的动作（会话日志），
 *      以及把 hub 的原话（404 未知 job / 409 本来就没冻）如实回显——三件事都是控制台的活；
 *    · 与「重发不清冻结」合起来看：重发不解除熔断，所以**没有这个按钮就等于永久烂在列表里**
 *      （§4.1 的可逆口不是可选项）。
 *
 *  鉴权与 hub 其余管理端点同源（`rl.remote_token`）。动作**不写任何盘上事实**：冻结态是 hub 的
 *  内存态（重启即复位），控制台只是转发一次人工确认。
 */

import { loadConfig } from '../../core/config'
import { hubCandidates, hubUnfreeze } from '../../stack/hub-admin'
import { ActionResult, done, guard, release } from './result'

/** 只接受 hub 那边的 job_id 形状（十六进制/短横线；它进 URL 查询串与 hub 日志）。 */
const JOB_ID_RE = /^[A-Za-z0-9._-]{1,64}$/

/** job_id 形状校验 → 人读错误（合法 = null）。
 *
 *  导出给 api 路由用：它是**参数错误（400）**，不是 busy（409）——两条口子判据必须同源，
 *  否则「框里空着点解冻」会拿到一个语义不对的 409（动作层与 api 层各自写一份正则就是漂移）。 */
export function jobIdError(jobId: unknown): string | null {
  const jid = String(jobId ?? '').trim()
  if (!jid) return '需要 job_id（解冻是逐 job 的动作）'
  if (!JOB_ID_RE.test(jid)) return `job_id 非法: ${jid}（hub 的 job_id 只含字母/数字/._-）`
  return null
}

/** 解冻一份被熔断冻住的 job（逐候选 hub 试，第一个成功即算）。 */
export async function unfreezeJob(course: string, jobId: string): Promise<ActionResult> {
  const jid = String(jobId ?? '').trim()
  guard(`poison-unfreeze:${jid || 'x'}`)
  try {
    // 防御性同源校验（api 层已按 400 挡过一次）：直连动作层也不许把脏 job_id 发出去。
    const bad = jobIdError(jid)
    if (bad) return done(false, bad)
    const cfg = loadConfig()
    const token = String(cfg.rl?.remote_token ?? '')
    const tried: string[] = []
    let last = '没有任何 hub 在应答（未启动 / 不可达）'
    for (const base of hubCandidates(cfg, String(course ?? ''))) {
      tried.push(base)
      const r = await hubUnfreeze(base, token, jid)
      if (r.ok) {
        return done(true, `job ${jid} 已解冻——回池可重领`, [
          `hub ${base}：冻结已清除，且「零回传」计数一并归零（下一次重领从头计数）`,
          '★ 解冻只解决「不该继续冻着」：若这份 payload 本身是坏的，它会再被领、再炸、' +
            '再冻（3 次后）。真修法是把新字节重打发布（新 job_id）——重发**不会**替你解冻。',
        ])
      }
      last = r.message
      // 404（未知 job）/409（本来就没冻）：已问到了一个认识它的 hub，不必再试别处。
      if (/未知 job|未被冻结/.test(last)) {
        return done(false, `未解冻 job ${jid}：${last}`, [
          `hub ${base} 的答复（原话）——「本来就没冻」与「解冻失败」是两件事，` +
            '先看队列里那一行的 frozen 块确认它到底冻没冻',
        ])
      }
    }
    return done(false, `未解冻 job ${jid}：${last}`, [
      `试过的 hub：${tried.join(', ') || '(无候选)'}——确认 hub-server 在跑、token 与 rl-config 一致`,
    ])
  } finally {
    release(`poison-unfreeze:${jid || 'x'}`)
  }
}
