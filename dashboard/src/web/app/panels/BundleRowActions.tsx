/** BundleRowActions.tsx — 课程**行内**的离线任务包操作（导出 / 下载 / 导入产物）。
 *
 *  ★ 2026-09-22 改版（用户指令）：首页独立「任务包」面板下线——每个离线课程的
 *  「文件导出/导入」能力挪到课程矩阵所在行的「操作」列，与「切离线/恢复在线」「暂停」
 *  并列。行内断言口径与面板时代一致：
 *    - 导出 = `exportTaskBundle`（只读快照，随时可导，不与训练抢锁）+ 轮询
 *      `/api/taskBundleInfo` 翻出 zip（5s 一刷，导出完成即自动亮「下载」）；
 *    - 下载 = 浏览器直接跳 `/api/taskBundle?course=…`（服务端带 attachment 文件名）；
 *    - 导入 = multipart 传 `deliver-<课>.zip` → `/api/deliverUpload`（回传产物 + 自动评估）。
 *  只读（LAN）语义沿用矩阵：按钮照常渲染可点，被拒由服务端 403 说明（§7 O1）。
 */

import { useEffect, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  fetchTaskBundleInfo,
  postAction,
  taskBundleDownloadUrl,
  uploadDeliverZip,
  type TaskBundleInfo,
} from '../lib/api-client'
import { fmtBytes } from '../../view'

export interface BundleRowActionsProps {
  course: string
}

/** 行内小条：`导出任务包 · 下载 · 导入`（+ 一条当前状态 flash）。
 *  轮询同时负责「导出完成自动出现下载」——状态不另起拉取通道。 */
export function BundleRowActions({ course }: BundleRowActionsProps) {
  const [info, setInfo] = useState<TaskBundleInfo | null>(null)
  const [busy, setBusy] = useState<'export' | 'import' | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      void fetchTaskBundleInfo(course)
        .then((i) => alive && setInfo(i))
        .catch(() => alive && setInfo(null))
    }
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [course])

  const finish = (msg: string | null): void => setFlash(msg)

  const onExport = async (): Promise<void> => {
    setBusy('export')
    try {
      const r = await postAction('exportTaskBundle', { course })
      finish(r.ok ? '导出已启动…' : (r.message ?? '导出启动失败'))
    } catch (e) {
      finish(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const onImport = async (file: File): Promise<void> => {
    setBusy('import')
    try {
      const r = await uploadDeliverZip(course, file)
      finish(r.ok ? (r.message ?? '导入完成') : (r.message ?? '导入失败'))
    } catch (e) {
      finish(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <span className="tc-mx__opgroup" role="group" aria-label={`任务包：${course}`}>
      <span className="tc-mx__opsep" aria-hidden="true" />
      <button
        type="button"
        className="tc-btn tc-btn--sm"
        disabled={busy === 'import'}
        aria-label={`导出任务包 ${course}`}
        title={
          '导出 task-<课>.zip（整段剩余，只读快照、随时可导）：hub 的 /offline/task-pack ' +
          '按课上架它，Kaggle/Colab 离线 worker 就绪后自动拉取执行'
        }
        onClick={() => void onExport()}
      >
        {busy === 'export' ? '导出中…' : '导出任务包'}
      </button>
      {info?.exists ? (
        <span className="tc-mx__bundleinfo" title={`${fmtBytes(info.bytes)} · ${info.log ?? ''}`}>
          <a className="tc-btn tc-btn--sm" href={taskBundleDownloadUrl(course)} download>
            下载
          </a>
          <span className="tc-caption">{fmtBytes(info.bytes)}</span>
        </span>
      ) : (
        <span className="tc-caption" title="还没有导出过任务包（点「导出任务包」生成）">
          未导出
        </span>
      )}
      <label className="tc-btn tc-btn--sm" aria-label={`导入产物 ${course}`}>
        导入
        <input
          type="file"
          accept=".zip"
          hidden
          disabled={busy !== null}
          onChange={(e: JSX.TargetedEvent<HTMLInputElement>) => {
            const f = e.currentTarget.files?.[0]
            if (!f) return
            void onImport(f)
            e.currentTarget.value = ''
          }}
        />
      </label>
      {flash ? (
        <span className="tc-caption" title={flash}>
          {flash.length > 60 ? `${flash.slice(0, 60)}…` : flash}
        </span>
      ) : null}
    </span>
  )
}
