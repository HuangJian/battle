/** WorkerRegistry.tsx — GPU push worker 登记入口（2026-09-18 用户口径）。
 *
 *  「登记」= upsert rl-config `nodes[]` 里那条 `gpu_push` 条目；hub 按 mtime 热重载它
 *  （另有一次即时叫醒，见动作层的 workers.ts）。于是本面板是**配置的编辑器 +
 *  探活的显示器**，不是第二份登记表——两处各存一份必然漂。
 *
 *  探活两列（刻意分开，因为它们回答不同问题）：
 *    · 面板直探 `{url}/ping`：**现在**这台能不能连上（登记那一刻的体检）；
 *    · hub 侧：hub 的周期探活结论——`hubOnline` 是调度器此刻认为它在不在线
 *      （它才是「job 会不会被推过去」的判据）。两列不一致本身是有用的信号
 *      （面板通而 hub 判离线 ⇒ hub 还没重载配置）。
 *
 *  只读（局域网）视图：按钮保持可点、悬停给提示，真点击由服务端 403 + flash 兜底
 *  （与组件卡同一哲学：只读是动作边界，不是把整个区域画成灰的）。 */

import { useState } from 'preact/hooks'
import { Switch } from '../../components/Switch'
import { shortUrl, type PushWorkerRegistryView, type PushWorkerView } from '../../view'

export interface WorkerRegistryProps {
  registry: PushWorkerRegistryView | null
  onAction: (act: string, body: Record<string, unknown>) => Promise<{ ok: boolean }>
  readOnly?: boolean
}

const RO_TITLE = '只读模式：登记/移除仅限本机 localhost'

/** 一行 worker 的状态点：hub 侧优先（它决定调度），回退面板直探。 */
function workerDot(w: PushWorkerView): { cls: string; title: string } {
  if (!w.enabled) return { cls: 'tc-dot--empty', title: '已停用：不参与 hub 派发，也不探活' }
  if (w.hubOnline === true)
    return { cls: 'tc-dot--on', title: 'hub 侧：在线（调度器会往它推 job）' }
  if (w.hubOnline === false) {
    return {
      cls: 'tc-dot--dead',
      title: 'hub 侧：离线——hub 探活未通过，job 不会推给它（检查 worker_server / 隧道 / authKey）',
    }
  }
  // hub 未登记（未启用 push 派发）→ 退回面板直探结论
  if (w.online === true)
    return { cls: 'tc-dot--warn', title: '面板直探通，但 hub 未登记它（hub 未启用 push 派发？）' }
  if (w.online === false) return { cls: 'tc-dot--dead', title: '面板直探 /ping 不通' }
  return { cls: 'tc-dot--empty', title: '未探（无鉴权键或已停用）' }
}

export function WorkerRegistry({ registry, onAction, readOnly }: WorkerRegistryProps) {
  const [open, setOpen] = useState(false)
  const workers = registry?.workers ?? []
  const mounted = registry?.mounted === true

  return (
    <section className="tc-wreg" aria-label="push worker 登记">
      <div className="tc-wreg__head">
        <span className="lbl">push worker</span>
        <b>{workers.length}</b>
        {mounted ? (
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
        )}
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
            readOnly ? RO_TITLE : '把一台 GPU worker_server 写进 rl-config（hub 热重载后即可接活）'
          }
          onClick={() => setOpen((v) => !v)}
        >
          {open ? '收起' : '+ 登记 worker'}
        </button>
      </div>
      {workers.length === 0 ? (
        <p className="tc-wreg__empty">
          还没有登记 push worker。push 模式下 hub 只往登记在册的节点推 job——没有登记时 训练侧按 pull
          走（云机自领）。
        </p>
      ) : (
        <div className="tc-wreg__rows">
          {workers.map((w) => {
            const d = workerDot(w)
            return (
              <span key={w.id} className="tc-npill tc-wreg__pill">
                <span className={`tc-dot ${d.cls}`} title={d.title} />
                <b>{w.id}</b>
                {w.local ? (
                  <b className="tc-wreg__local" title="本机回落节点（rl-config local_push）">
                    本机
                  </b>
                ) : null}
                <span className="tc-wreg__url" title={w.url}>
                  {shortUrl(w.url)}
                </span>
                <span className="tc-wreg__conc" title={`并发数 ${w.concurrency}`}>
                  ×{w.concurrency}
                </span>
                {w.busy === true ? (
                  <span className="tc-wreg__busy" title="worker 自报在跑活（/ping busy）">
                    忙
                  </span>
                ) : null}
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
                    readOnly ? RO_TITLE : '从 rl-config 移除（指向它的课程 push_node_url 一并清掉）'
                  }
                  onClick={() => void onAction('removePushWorker', { id: w.id })}
                >
                  移除
                </button>
              </span>
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
