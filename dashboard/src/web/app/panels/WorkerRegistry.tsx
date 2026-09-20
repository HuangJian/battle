/** WorkerRegistry.tsx — GPU push worker 登记入口（2026-09-18 用户口径）。
 *
 *  「登记」= upsert rl-config `nodes[]` 里那条 `gpu_push` 条目；hub 按 mtime 热重载它
 *  （另有一次即时叫醒，见动作层的 workers.ts）。于是本面板是**配置的编辑器 +
 *  探活的显示器**，不是第二份登记表——两处各存一份必然漂。
 *
 *  2026-09-20（docs/dashboard-redesign.md P1）：行/头/空态迁移到 `StatusRow` /
 *  `SectionHeader` / `Empty` 三个原语，行为与文案不变。
 *
 *  探活两列（刻意分开，因为它们回答不同问题）：
 *    · 面板直探 `{url}/ping`：**现在**这台能不能连上（登记那一刻的体检）；
 *    · hub 侧：hub 的周期探活结论——`hubOnline` 是调度器此刻认为它在不在线
 *      （它才是「job 会不会被推过去」的判据）。两列不一致本身是有用的信号
 *      （面板通而 hub 判离线 ⇒ hub 还没重载配置）。
 *
 *  只读（局域网）视图：按钮保持可点、悬停给提示，真点击由服务端 403 + flash 兜底
 *  （与组件卡同一哲学：只读是动作边界，不是把整个区域画成灰的）。
 */

import { useState } from 'preact/hooks'
import { Switch } from '../../components/Switch'
import { Empty } from '../../components/Empty'
import { SectionHeader } from '../../components/SectionHeader'
import { StatusRow, type RowBadge, type RowMeta } from '../../components/StatusRow'
import type { StatusTone } from '../../components/StatusDot'
import { shortUrl, type PushWorkerRegistryView, type PushWorkerView } from '../../view'

export interface WorkerRegistryProps {
  registry: PushWorkerRegistryView | null
  onAction: (act: string, body: Record<string, unknown>) => Promise<{ ok: boolean }>
  readOnly?: boolean
}

const RO_TITLE = '只读模式：登记/移除仅限本机 localhost'

/** 一行 worker 的语义档 + 状态点解释：hub 侧优先（它决定调度），回退面板直探。 */
function workerTone(w: PushWorkerView): { tone: StatusTone; title: string } {
  if (!w.enabled) return { tone: 'off', title: '已停用：不参与 hub 派发，也不探活' }
  if (w.hubOnline === true) return { tone: 'ok', title: 'hub 侧：在线（调度器会往它推 job）' }
  if (w.hubOnline === false) {
    return {
      tone: 'err',
      title: 'hub 侧：离线——hub 探活未通过，job 不会推给它（检查 worker_server / 隧道 / authKey）',
    }
  }
  // hub 未登记（未启用 push 派发）→ 退回面板直探结论
  if (w.online === true)
    return { tone: 'warn', title: '面板直探通，但 hub 未登记它（hub 未启用 push 派发？）' }
  if (w.online === false) return { tone: 'err', title: '面板直探 /ping 不通' }
  return { tone: 'off', title: '未探（无鉴权键或已停用）' }
}

export function WorkerRegistry({ registry, onAction, readOnly }: WorkerRegistryProps) {
  const [open, setOpen] = useState(false)
  const workers = registry?.workers ?? []
  const mounted = registry?.mounted === true

  return (
    <section className="tc-wreg" aria-label="push worker 登记">
      <SectionHeader
        title="push worker"
        count={workers.length}
        hint="登记 = 写进 rl-config.json 的 nodes[]（gpu_push）。hub 按 mtime 热重载，另有一次即时叫醒"
        note={
          mounted ? (
            <span
              className="tc-wreg__mount"
              title="hub-server 已带 --push：它按队列顺序把 job 推给这些 worker"
            >
              hub 已挂载派发
            </span>
          ) : (
            <span
              className="tc-wreg__mount tc-wreg__mount--off"
              title="hub 未带 --push（或没有 hub 在应答）：登记会落进 rl-config，但 hub 不会真派发——训练侧仍走 pull"
            >
              hub 未挂载派发
            </span>
          )
        }
        actions={
          <>
            {/* 派发开关（rl.hub_push，缺省开）：push 的**唯一**模式开关。关掉 = 训练侧
                直推登记节点（不经 hub 队列），所以它与登记表同屏——「配了节点走哪条路」
                在这里一眼可见、可改，不必手改 rl-config.json。 */}
            <label
              className="tc-wreg__hubpush"
              title={
                (readOnly ? `${RO_TITLE}；` : '') +
                'rl.hub_push：开（缺省）= hub 按队列顺序推给空闲 worker（超时回落队首换人）；' +
                '关 = 训练侧按登记顺序直连节点。改动写 rl-config，训练栈重启后生效。'
              }
            >
              <Switch
                label="hub 中介派发"
                checked={registry?.hubPush !== false}
                onChange={(v) =>
                  void onAction('setMode', { key: 'rl.hub_push', value: v ? '1' : '0' })
                }
              />
              <span className="tc-muted tc-small">hub 派发</span>
            </label>
            <button
              type="button"
              className="tc-btn tc-btn--sm"
              aria-label="重新载入 hub 的 worker 登记表"
              title={readOnly ? RO_TITLE : '让 hub 立刻重读 rl-config（不等下一拍热重载）'}
              onClick={() => void onAction('reloadPushWorkers', {})}
            >
              ⟳ 重载
            </button>
            <button
              type="button"
              className={`tc-btn tc-btn--sm${open ? '' : ' tc-btn--primary'}`}
              aria-expanded={open}
              aria-label={open ? '收起登记表单' : '登记 worker'}
              title={
                readOnly
                  ? RO_TITLE
                  : '把一台 GPU worker_server 写进 rl-config（hub 热重载后即可接活）'
              }
              onClick={() => setOpen((v) => !v)}
            >
              {open ? '收起' : '+ 登记 worker'}
            </button>
          </>
        }
      />
      {workers.length === 0 ? (
        <Empty kind="empty" reason="还没有登记 push worker">
          hub 只往登记在册的节点推 job——没有登记时训练侧按 pull 走（云机/本机 worker 自己来领，
          本机只需 hub 在线）。
        </Empty>
      ) : (
        <div className="tc-wreg__rows">
          {workers.map((w) => {
            const { tone, title } = workerTone(w)
            const meta: RowMeta[] = [
              { text: shortUrl(w.url), title: w.url, mono: true },
              { text: `×${w.concurrency}`, title: `并发数 ${w.concurrency}` },
            ]
            const badges: RowBadge[] =
              w.busy === true
                ? [{ text: '忙', tone: 'y', title: 'worker 自报在跑活（/ping busy）' }]
                : []
            return (
              <StatusRow
                key={w.id}
                tone={tone}
                dotTitle={title}
                name={w.id}
                meta={meta}
                badges={badges}
                ariaLabel={`worker ${w.id}，${title}`}
                actions={
                  <>
                    <Switch
                      label={`${w.enabled ? '停用' : '启用'} ${w.id}`}
                      checked={w.enabled}
                      onChange={(v) => void onAction('setNodeEnabled', { id: w.id, enabled: v })}
                    />
                    <button
                      type="button"
                      className="tc-btn tc-btn--sm"
                      aria-label={`移除 worker ${w.id}`}
                      title={
                        readOnly
                          ? RO_TITLE
                          : '从 rl-config 移除（指向它的课程 push_node_url 一并清掉）'
                      }
                      onClick={() => void onAction('removePushWorker', { id: w.id })}
                    >
                      移除
                    </button>
                  </>
                }
              />
            )
          })}
        </div>
      )}
      {open ? (
        <WorkerForm
          onSubmit={async (fields) => {
            // 展开成普通对象：动作层的 body 是 `Record<string, unknown>` 契约
            // （接口类型没有索引签名，直接传会 TS2345）。
            const r = await onAction('registerPushWorker', { ...fields })
            if (r.ok) setOpen(false)
            return r.ok
          }}
        />
      ) : null}
    </section>
  )
}

/** 登记表单字段（与 POST body 一字不差）。 */
export interface WorkerFields {
  id: string
  url: string
  authKey: string
  concurrency: number
}

/** 表单是否填齐（保存键的禁用条件；纯函数，可单测）。
 *
 *  只做「非空」这一层——**格式校验在服务端**（id 字符域 / url 协议 / 并发域），
 *  面板不写第二份口径（写两份必然漂：服务端拒了面板却显示可保存）。 */
export function workerFormReady(f: {
  id: string
  url: string
  authKey: string
  concurrency: string
}): boolean {
  return (
    f.id.trim() !== '' &&
    f.url.trim() !== '' &&
    f.authKey.trim() !== '' &&
    f.concurrency.trim() !== ''
  )
}

/** 登记表单（独立组件：**不依赖开合状态**，故可直接 SSR 断言字段与按钮）。 */
export function WorkerForm({ onSubmit }: { onSubmit: (fields: WorkerFields) => Promise<boolean> }) {
  const [id, setId] = useState('')
  const [url, setUrl] = useState('')
  const [authKey, setAuthKey] = useState('')
  const [concurrency, setConcurrency] = useState('1')
  const [pending, setPending] = useState(false)
  const fields = { id, url, authKey, concurrency }
  return (
    <div className="tc-wreg__form">
      <label>
        <span className="tc-wreg__lbl">id</span>
        <input
          type="text"
          aria-label="worker id"
          placeholder="gpu-4090"
          value={id}
          onInput={(e) => setId((e.target as HTMLInputElement).value)}
        />
      </label>
      <label>
        <span className="tc-wreg__lbl">url</span>
        <input
          type="text"
          aria-label="worker url"
          placeholder="https://xxx.trycloudflare.com"
          value={url}
          onInput={(e) => setUrl((e.target as HTMLInputElement).value)}
        />
      </label>
      <label>
        <span className="tc-wreg__lbl">authKey</span>
        <input
          type="text"
          aria-label="worker authKey"
          placeholder="worker_server --token"
          value={authKey}
          onInput={(e) => setAuthKey((e.target as HTMLInputElement).value)}
        />
      </label>
      <label>
        <span className="tc-wreg__lbl">并发</span>
        <input
          type="number"
          min={1}
          max={64}
          aria-label="worker 并发数"
          value={concurrency}
          onInput={(e) => setConcurrency((e.target as HTMLInputElement).value)}
        />
      </label>
      <button
        type="button"
        className="tc-btn tc-btn--sm tc-btn--primary"
        disabled={pending || !workerFormReady(fields)}
        aria-label="保存 worker 登记"
        title="写进 rl-config.json 的 nodes[]（gpu_push）；同 id 即更新"
        onClick={() => {
          setPending(true)
          void onSubmit({
            id: id.trim(),
            url: url.trim(),
            authKey: authKey.trim(),
            concurrency: Number(concurrency),
          }).finally(() => setPending(false))
        }}
      >
        保存
      </button>
    </div>
  )
}
