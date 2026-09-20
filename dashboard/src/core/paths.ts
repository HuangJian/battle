/** paths.ts — 路径常量（dashboard 全项目共享，路径事实唯一来源）。
 *
 *  本文件是 dashboard 内**唯一**允许用 `import.meta.dir` 上溯推导路径的地方；
 *  其余模块一律从这里 import 常量，绝不自算相对深度（目录搬迁时只有这里会错，
 *  且一错即 typecheck 之外的运行时路径立刻暴露）。
 */

import path from 'path'

/** dashboard/ 项目根（本文件位于 dashboard/src/core/，上溯两级）。 */
export const DASHBOARD_ROOT = path.resolve(import.meta.dir, '..', '..')
/** 仓库根（dashboard/ 的上一级）。 */
export const REPO_ROOT = path.resolve(DASHBOARD_ROOT, '..')
/** nn-training/（python 训练包根）。 */
export const NN_TRAINING = path.join(REPO_ROOT, 'nn-training')
/** rl-config.json（训练/节点单一配置事实来源）。 */
export const CONFIG_PATH = path.join(NN_TRAINING, 'rl-config.json')

/** rl-config 路径（单测以 BCITY_RL_CONFIG 重定向到临时文件——读线上配置做
 *  写回测试会污染工作配置，故这里**惰性**取值；默认行为零变化）。 */
export function configPath(): string {
  return process.env.BCITY_RL_CONFIG ?? CONFIG_PATH
}
/** 课程目录。 */
export const CURRICULA_DIR = path.join(NN_TRAINING, 'curricula')
/** 课程目录（单测以 BCITY_CURRICULA_DIR 重定向到临时目录——课程夹具不再依赖
 *  机器上真实存在的权重/课程文件（F-B6 教训）；默认行为零变化，故**惰性**取值）。 */
export function curriculaDir(): string {
  return process.env.BCITY_CURRICULA_DIR ?? CURRICULA_DIR
}
/** 运行日志/登记/临时文件根（仓库根 tmp/——2026-09-08 统一：全项目只使用 ./tmp，
 *  不再用 nn-training/tmp，消除双 tmp 混淆）。 */
export const LOG_DIR = path.join(REPO_ROOT, 'tmp')

/** 课程运行日志根（与 LOG_DIR 同；单测以 BCITY_TMP_LOGS_DIR 重定向到临时目录——课程
 *  发现/总览测试不再依赖机器上 tmp/ 的真实课程数据；默认行为零变化，故**惰性**取值）。 */
export function tmpLogsDir(): string {
  return process.env.BCITY_TMP_LOGS_DIR || LOG_DIR
}
/** 池历史（各训练流的 `dist-agent-meta.jsonl`）扫描根。
 *
 *  单测以 `BCITY_POOL_DIR` 重定向到临时目录——聚合要用**真目录树 + 真账本**才能验
 *  「贡献数取最近完成轮」（夹具写进仓根 tmp/ 会被真实训练流淹没，也会反过来污染
 *  别的用例对活跃流的判定）；默认行为零变化，故**惰性**取值。 */
export function tmpPoolDir(): string {
  return process.env.BCITY_POOL_DIR || LOG_DIR
}

/** 本模块的运行日志目录（tmp/training-start）。 */
export const START_LOG_DIR = path.join(LOG_DIR, 'training-start')
/** 监控页热加载元数据文件（变更检测哨兵，实义为最后实际变更时间戳）。 */
export const MONITOR_TOUCH = path.join(START_LOG_DIR, 'monitor-touch.json')

/** 控制台**会话日志**（组件级决策：启/停/重启/判死/开课/停课…）。
 *
 *  与 `launch/cli.ts` 的 `initLog('train-cli')`（每次运行一个新文件）分工不同：控制台是
 *  **长驻进程**，操作员要的是一个固定路径能翻到全部决策——所以文件名稳定，每次启动往同一份
 *  里追加一行会话头（`=== console session <ISO> pid=N ===`），会话仍有边界。
 *  单测以 `BCITY_CONSOLE_LOG` 重定向到临时文件（否则跑测试会往仓根 tmp/ 写），故**惰性**取值。 */
export function consoleLogPath(): string {
  return process.env.BCITY_CONSOLE_LOG ?? path.join(START_LOG_DIR, 'console.log')
}

/** 控制台状态文件（trainer 模式 / 当前课程 / 云端停机记录）。
 *  单测以 BCITY_CONSOLE_STATE 重定向到临时目录——故这里**惰性**取值。 */
export function consoleStatePath(): string {
  return process.env.BCITY_CONSOLE_STATE ?? path.join(START_LOG_DIR, 'console-state.json')
}

/** 训练侧控制意图文件（`tmp/loop-control.json`）—— 控制台**写**、单进程 supervisor **读**。
 *
 *  它是「暂停/恢复某课」的唯一通道（用户 2026-09-18 定案：不为此在训练进程里再挂一个
 *  HTTP 服务）。两侧共享同一个工作区 ⇒ 一份意图文件最省，且 hub 挂了也能用。
 *  **python 侧默认路径必须与这里一致**：`nn-training/rl/loop_control.py::control_path()`
 *  （那边 `NN_LOOP_CONTROL` 可覆盖，本侧用 `BCITY_LOOP_CONTROL`；单测各自重定向）。
 *  故这里**惰性**取值。 */
export function loopControlPath(): string {
  return process.env.BCITY_LOOP_CONTROL ?? path.join(LOG_DIR, 'loop-control.json')
}

/** 训练进程的控制**回执**（`tmp/loop-control.applied.json`）—— 训练侧写、控制台读。
 *
 *  内容 = 「我（pid）此刻实际把哪几门课暂停着」。它存在的唯一理由：意图文件回答不了
 *  「生效了没」——进程可能没在跑，也可能还没轮到读文件。
 *  对应 `nn-training/rl/loop_control.py::applied_path()`（`NN_LOOP_CONTROL_APPLIED`）。 */
export function loopAppliedPath(): string {
  return process.env.BCITY_LOOP_APPLIED ?? path.join(LOG_DIR, 'loop-control.applied.json')
}

/** 控制台客户端 bundle 产物目录（server/build.ts 构建，.gitignore 排除）。 */
export const BUNDLE_DIR = path.join(DASHBOARD_ROOT, '.build')
/** EvalBoard 账本数据根（EVALBOARD_DATA 环境变量可覆盖；见 store.ts）。 */
export const EVALBOARD_DATA_DIR = path.join(DASHBOARD_ROOT, 'data', 'evalboard')
/** EvalBoard 阶梯定义正本（入库副本，随代码走；工作副本在 EVALBOARD_DATA_DIR）。 */
export const LADDER_CANON_PATH = path.join(DASHBOARD_ROOT, 'src', 'evalboard', 'ladder.json')

export function fmtStamp(d = new Date()): string {
  return d.toISOString().replace(/[:.]/g, '-').slice(0, 19)
}
