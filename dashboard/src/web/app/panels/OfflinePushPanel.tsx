/** OfflinePushPanel.tsx — 离线 push 派发器的控制台读面（W6-3，plan/push-mode-notebooks §6.7）。
 *
 *  数据源 = hub `/admin/offline-push-workers`（与总览同一次 5s 探测缓存，零新增网络）。
 *  **只读**：派单由 hub 按队列自己拍，撤销在 hub 的动作面（`POST /offline/push/cancel` /
 *  停课标记），控制台不再造第二个开关——这块只回答两件事：
 *    · **在途单拉到第几轮**（assignment 账本：谁在跑哪门课、cursor 到哪）；
 *    · **每台 worker 能不能接单**（在线 / 忙闲 / 协议门——`proto_ok !== true` 就不派）。
 *
 *  两个空态词不同：「没有 hub 应答」是**未知**，「hub 没挂 `--offline-push`」是**确定的否定**
 *  ——把它们说成一句话会让运维去查错方向（`mounted` 为 `null` 是第三种：端点这一拍没答上）。
 */

import { Empty } from '../../components/Empty'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusRow, type RowBadge, type RowMeta } from '../../components/StatusRow'
import type { StatusTone } from '../../components/StatusDot'
import {
  fmtAge,
  shortUrl,
  type OfflinePushAssignmentView,
  type OfflinePushView,
  type OfflinePushWorkerView,
} from '../../view'

export interface OfflinePushPanelProps {
  view: OfflinePushView | null
}

function mountNote(view: OfflinePushView | null) {
  if (!view) {
    return (
      <span
        className="tc-wreg__mount tc-wreg__mount--off"
        title="没有 hub 在应答：离线 push 的派发器读面住在 hub 上（/admin/offline-push-workers）"
      >
        hub 不可达
      </span>
    )
  }
  if (view.mounted === true) {
    return (
      <span
        className="tc-wreg__mount"
        title="hub-server 已带 --offline-push：出站派单给云 notebook，逐轮拉回产物，段末入账"
      >
        hub 已挂载离线派发
      </span>
    )
  }
  if (view.mounted === false) {
    return (
      <span
        className="tc-wreg__mount tc-wreg__mount--off"
        title="hub 明确答了 409/404：网关没挂离线 push 派发（启动没带 --offline-push），不会有派单"
      >
        hub 未挂载离线派发
      </span>
    )
  }
  return (
    <span
      className="tc-wreg__mount tc-wreg__mount--off"
      title="hub 在应答 /admin/queue，但派发器读面这一拍没答上——未知，下一拍自动重试"
    >
      派发器读面未知
    </span>
  )
}

/** assignment 的语义档：`conflict` 是 hub 记的入账撞车（会走取消收摊），最需要醒目。 */
function assignmentTone(a: OfflinePushAssignmentView): { tone: StatusTone; title: string } {
  if (a.conflict) {
    return { tone: 'err', title: `hub 记了冲突：${a.conflict}——这一单在取消收摊` }
  }
  switch (a.phase) {
    case 'running':
      return { tone: 'ok', title: '云机已受理并接管：它在跑，hub 逐轮拉取入账' }
    case 'dispatching':
      // 2026-10-24（用户口径）：包就绪 ≠ 接管 —— 这一刻只是「提议」，hold 还没写。
      return { tone: 'warn', title: '提议中：正在投任务包 / 等云机受理（受理之后才接管）' }
    case 'settling':
      return { tone: 'warn', title: '收摊重试中：ack / cancel 没答，hub 会按退避重放' }
    default:
      return { tone: 'off', title: `未知阶段：${a.phase || '-'}（hub 可能比控制台新）` }
  }
}

/** worker 的语义档：**协议门优先**——在线但不兼容照样不会被派单，绿色会误导。 */
function workerTone(w: OfflinePushWorkerView): { tone: StatusTone; title: string } {
  if (!w.online) return { tone: 'err', title: 'hub 探活不通（检查 notebook / 隧道 / authKey）' }
  if (w.protoOk === false) {
    return {
      tone: 'err',
      title: `协议门没过（proto=${w.proto || '?'}）——升级它的 notebook 代码，hub 不会向它派单`,
    }
  }
  if (w.protoOk === null) {
    return { tone: 'warn', title: '协议门还没问出结论（/offline/push/state 没答上）——不知道就不派' }
  }
  if (w.busy) return { tone: 'ok', title: '在线、协议兼容，此刻在跑一单' }
  return { tone: 'ok', title: '在线、协议兼容、空闲——可接下一单' }
}

function assignmentRow(a: OfflinePushAssignmentView) {
  const { tone, title } = assignmentTone(a)
  const badges: RowBadge[] = [{ text: a.phase || '?', tone: a.phase === 'running' ? 'g' : 'y' }]
  if (a.conflict) badges.push({ text: '冲突', tone: 'r', title: a.conflict })
  const meta: RowMeta[] = [
    { text: a.workerId || '?', title: `worker ${a.workerId || '-'}`, mono: true },
    {
      text: a.taskId ? a.taskId.slice(0, 8) : '-',
      title: `task_id ${a.taskId || '-'}`,
      mono: true,
    },
    { text: `${a.rounds} 轮`, title: `已入账 ${a.rounds} 轮（cursor_it=${a.cursorIt}）` },
  ]
  const age =
    a.updatedAt > 0 ? fmtAge(Math.max(0, Math.floor(Date.now() / 1000) - a.updatedAt)) : ''
  if (age) {
    meta.push({
      text: `${age}前`,
      title: `最后推进：${new Date(a.updatedAt * 1000).toLocaleString()}`,
    })
  }
  return (
    <StatusRow
      key={a.course}
      tone={tone}
      dotTitle={title}
      name={a.course}
      value={`it ${a.cursorIt}`}
      valueTitle="hub 已入账的最高一轮"
      badges={badges}
      meta={meta}
      ariaLabel={`离线 push 单 ${a.course}，${title}`}
    />
  )
}

function workerRow(w: OfflinePushWorkerView) {
  const { tone, title } = workerTone(w)
  const badges: RowBadge[] = []
  if (w.busy) badges.push({ text: '忙', tone: 'y', title: 'hub 侧读到的 busy（在跑一单）' })
  if (w.protoOk === false)
    badges.push({ text: 'proto 不符', tone: 'r', title: w.proto || '未知协议' })
  else if (w.protoOk === null) badges.push({ text: 'proto ?', tone: 'gray' })
  if (w.lastErr) badges.push({ text: '报错', tone: 'r', title: w.lastErr })
  const meta: RowMeta[] = [
    { text: shortUrl(w.url), title: w.url, mono: true },
    { text: `队 ${w.queued}`, title: `hub 侧队列深度 ${w.queued}` },
    { text: `完 ${w.done}`, title: `派发器累计完成 ${w.done} 单` },
  ]
  if (w.misses > 0) meta.push({ text: `miss ${w.misses}`, title: `连续探活失败 ${w.misses} 次` })
  return (
    <StatusRow
      key={w.id}
      tone={tone}
      dotTitle={title}
      name={w.id}
      badges={badges}
      meta={meta}
      ariaLabel={`离线 push worker ${w.id}，${title}`}
    />
  )
}

export function OfflinePushPanel({ view }: OfflinePushPanelProps) {
  const assignments = view?.assignments ?? []
  const workers = view?.workers ?? []
  const mounted = view?.mounted === true
  return (
    <section className="tc-wreg" aria-label="离线 push 派发">
      <SectionHeader
        title="离线 push"
        count={mounted ? assignments.length : undefined}
        hint="hub 出站派单给云 notebook：任务包推过去、轮产物逐轮拉回、段末结果入账（plan/push-mode-notebooks）"
        note={mountNote(view)}
      />
      {!view ? (
        <Empty kind="empty" reason="没有 hub 在应答，读不到离线 push 派发器">
          hub 不可达时这块是「未知」：派发器状态住在 hub 的<code>/admin/offline-push-workers</code>
          ，控制台不自己推算第二份。
        </Empty>
      ) : !mounted ? (
        <Empty
          kind="empty"
          reason={
            view.mounted === false
              ? 'hub 未启用离线 push 派发（启动没带 --offline-push）'
              : '派发器读面这一拍没答上（hub 在应答，端点探测失败）'
          }
        >
          {view.mounted === false
            ? '离线 push 是 hub-server 的挂载能力：带 --offline-push 启动后，它才会按队列出站派单。'
            : '下一拍会自动重试；连续失败时查 hub 日志里的 /admin/offline-push-workers 请求。'}
        </Empty>
      ) : (
        <>
          <SectionHeader
            sub
            title="在途 assignment"
            count={assignments.length}
            hint="hub 的派单账本：谁在跑哪门课、已入账到第几轮（唯一读面）"
          />
          {assignments.length === 0 ? (
            <Empty kind="empty" reason="此刻没有在途单（派发器空闲，或所有单已收摊）" />
          ) : (
            <div className="tc-wreg__rows">{assignments.map(assignmentRow)}</div>
          )}
          <SectionHeader
            sub
            title="离线 worker"
            count={workers.length}
            hint="登记在 hub 的离线 worker：在线 / 忙闲 / 协议门（proto_ok 不为 true 就不会被派单）"
          />
          {workers.length === 0 ? (
            <Empty kind="empty" reason="hub 的离线 worker 登记表是空的">
              在 rl-config 的 <code>nodes[]</code> 里登记 worker（或经 GPU push 面板），hub
              热重载后填上。
            </Empty>
          ) : (
            <div className="tc-wreg__rows">{workers.map(workerRow)}</div>
          )}
        </>
      )}
    </section>
  )
}
