/** paths.ts — 仓库路径常量（tools/training/** 共享）。 */

import path from 'path'

/** 仓库根（tools/training/ 的上两级）。 */
export const REPO_ROOT = path.resolve(import.meta.dir, '..', '..')
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
/** 本模块的运行日志目录（tmp/training-start）。 */
export const START_LOG_DIR = path.join(LOG_DIR, 'training-start')
/** 监控页热加载元数据文件（变更检测哨兵，实义为最后实际变更时间戳）。 */
export const MONITOR_TOUCH = path.join(START_LOG_DIR, 'monitor-touch.json')

/** 控制台状态文件（trainer 模式 / 当前课程 / 云端停机记录）。
 *  单测以 BCITY_CONSOLE_STATE 重定向到临时目录——故这里**惰性**取值。 */
export function consoleStatePath(): string {
  return process.env.BCITY_CONSOLE_STATE ?? path.join(START_LOG_DIR, 'console-state.json')
}

export function fmtStamp(d = new Date()): string {
  return d.toISOString().replace(/[:.]/g, '-').slice(0, 19)
}
