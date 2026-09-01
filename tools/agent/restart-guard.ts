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
 * dist_common.request_upgrade_guarded（跨代去重 + 脏工作区拒发）构成双层收敛。
 */
/** grace 窗口：覆盖协调器 rescan 周期（~15s）的两个周期，实测回声 ≤1 个周期到达。 */
export const RESTART_GRACE_MS = 30_000

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
