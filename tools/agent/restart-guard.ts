/**
 * restart-guard.ts — /v1/restart 防重启循环护栏（纯函数，sampler-agent 与单测共享）。
 *
 * 背景（2026-09-01 远控重启循环事故）：协调器的 ping 门 / rescan 周期（~15s）短于
 * 「重启 → 新实例起听 → 协调器观察到新 codeHash」的链路延迟时，协调器仍见旧 hash
 * ⇒ 再次 POST /v1/restart ⇒ 新实例刚起听就被杀 ⇒ 无限重启循环（实测四连杀，
 * 节点始终无法贡献；用户手动重启的进程同样被杀）。
 *
 * 处置：进程启动后 RESTART_GRACE_MS 内拒绝一切 /v1/restart（409）——旧实例退出
 * 回声与协调器重扫回声都被吸收，协调器下轮 rescan 再试。与训练侧
 * common.distribution.request_upgrade_guarded（跨代去重 + 脏工作区拒发）构成双层收敛。
 */
/** grace 窗口：覆盖协调器 rescan 周期（~15s）的两个周期，实测回声 ≤1 个周期到达。 */
export const RESTART_GRACE_MS = 30_000

/**
 * 外部 supervisor 标记（`rollout.ipynb` 的 `rollout_node` 注入）。
 *
 * 有它时 `/v1/restart` **不 spawn detached child**：agent 自己退出，由 supervisor 重新拉起。
 * 否则 agent 的交接 child 会和 supervisor 的新进程抢同一个端口/单实例锁，远控重启会把
 * 节点监护循环打崩（notebook 启动的 sampler 与 console 启动的 sampler 的区别就在这里）。
 */
export const SUPERVISED_ENV = 'SAMPLER_SUPERVISED'

/** 受监护重启的退出码（EX_TEMPFAIL）：supervisor 据此识别「计划内重启」，不计崩溃次数。 */
export const SUPERVISED_RESTART_EXIT_CODE = 75

/**
 * nowMs − bootMs ≥ graceMs 才接受重启；graceMs ≤ 0 视为禁用护栏（恒接受，测试用）。
 */
export function shouldAcceptRestart(
  nowMs: number,
  bootMs: number,
  graceMs: number = RESTART_GRACE_MS,
): boolean {
  if (graceMs <= 0) return true
  return nowMs - bootMs >= graceMs
}
