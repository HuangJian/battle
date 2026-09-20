/** component-roles.ts — 服务角色名（页面上怎么称呼它）与用途释义（悬停）。用户指令 2026-09-20。
 *
 *  ★ **key 仍是机器身份，不改也不撤**：`/log/<key>`、动作（`start:trainingLoop`）、
 *  账本条目、python 进程名/日志、服务端消息全部锚在 key 上。本模块只回答一个问题：
 *  **页面上把这个服务叫什么**。于是 key 出现在悬停里（`名称 · key`），行内只有角色名。
 *
 *  为什么名字要换（用户的理由）：`trainingLoop` / `hubServer` / `selfNode` / `cloudflared` /
 *  `localWorker` 是**实现名**——它们同时是进程名、模块名、URL 片段，读起来要先把技术栈在
 *  脑子里过一遍；而这一列要回答的日常问题是「谁在干活、干到哪一步」。角色名（管事 / 门房 /
 *  采办 / 跑腿 / 丹徒）一眼就知道各自身份，具体对应的进程与协议留在悬停里按需展开。
 *
 *  为什么释义（purpose）必须**逐行常驻悬停**，而不是放在族标题里：族标题只能笼统说
 *  「一个进程服务所有课程」，而这五行各自的作用完全不同（队列 / 训练 / 采集 / 隧道 / 本机算力）；
 *  行内空间只够一个角色名 ⇒ 悬停是它唯一的释义载体，所以每句都要自足（说清做什么 + 机器名）
 *  —— 不能写成「同上」或只写半句。
 *
 *  本模块在**客户端 bundle** 里：纯数据 + 纯函数，无 IO、无 node: 模块（与 view 层其余模块同规）。
 */

/** 一个服务的页面身份：角色名（上屏）+ 用途释义（悬停）。 */
export interface ComponentRole {
  /** 页面上的名字（用户给定的角色名）。 */
  name: string
  /** 悬停释义：这个服务干什么用（自足整句，含机器名/协议要点）。 */
  purpose: string
}

/** 服务角色表（键 = `ComponentView.key`，与 `core/types.ts` 的 Component 同名）。
 *
 *  ★ 顺序在这里**不重要**（上屏顺序由 `component-groups.ORDER` 决定，那里有它自己的理由）；
 *  本表只做「key → 叫什么 / 干什么」。
 */
export const COMPONENT_ROLES: Record<string, ComponentRole> = {
  trainingLoop: {
    name: '管事',
    purpose:
      '训练循环本体（trainingLoop / trainer）：领这门课的 rollout 数据、跑 PPO/BC 更新、' +
      '分发新权重、写训练账本。一个进程服务所有并行课程 —— 它在跑，这门课才在学。',
  },
  hubServer: {
    name: '门房',
    purpose:
      '作业中枢（hubServer）：PPO/评估作业的排队、派发与结果回收，并自己扫出哪些课在跑' +
      '（--discover）。本机 worker 与云机 worker 同权 —— 谁轮询 hub，谁就领到活。',
  },
  selfNode: {
    name: '采办',
    purpose:
      '本机采样节点（selfNode / sampler-agent，端口 8443）：把本机挂成一个 rollout 执行面 —— ' +
      'trainer 每轮把权重推过来，它跑对局并把结果回传。本机采集面全机只有这一份。',
  },
  cloudflared: {
    name: '跑腿',
    purpose:
      '入站隧道（cloudflared）：把本机 hub 暴露成一个公网 https 地址，云机 worker 靠它连回来。' +
      '一条隧道服务所有课程（多开只是多一份出网状态）。',
  },
  localWorker: {
    name: '丹徒',
    purpose:
      '本机 PPO worker（localWorker）：轮询本机 hub 领 PPO 作业，在本机 CPU 上算完回传。' +
      '与云机 worker 同一份代码与协议，一个进程串行干完所有课的活。',
  },
}

/** 页面名（未知 key ⇒ **回落 key 本身**：组件永不在 UI 上变成空白，只是没有别名）。 */
export function componentName(key: string): string {
  return COMPONENT_ROLES[key]?.name ?? key
}

/** 服务用途（悬停文案）；未知 key ⇒ 空串（调用方据此不出 title，而不是挂一个空提示）。 */
export function componentPurpose(key: string): string {
  return COMPONENT_ROLES[key]?.purpose ?? ''
}

/** 行/链接的悬停文案：`角色名（key）—— 用途`。
 *
 *  为什么把 key 拼进去：key 是唯一能在日志、URL、账本里对上号的东西；悬停是它在上屏面的
 *  唯一落脚处（行内只有角色名）。未知 key 时不重复（name 就是 key）。 */
export function componentHover(key: string): string {
  const role = COMPONENT_ROLES[key]
  if (!role) return ''
  return `${role.name}（${key}）—— ${role.purpose}`
}
