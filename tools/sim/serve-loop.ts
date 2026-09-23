/**
 * serve-loop.ts —— 长驻 worker 协议（`--serve`）的**唯一实现**。
 *
 * 协议：stdin 每行 = 一个任务的 argv（JSON 数组，**不含入口路径** —— agent 侧送的是
 * `args.slice(1)`）；跑完一局打印 `__SERVE_OK__`、失败打印 `__SERVE_ERR__ <msg>`，随后继续
 * 等下一行；启动就绪先打印 `__SERVE_READY__`。导出器自己的对局日志会混在同一路 stdout 上，
 * agent 只认 `__SERVE_*__` 标记行（见 sampler-agent.ts 的 `runViaPersistWorker`）。
 *
 * 为什么值得（plan/rollout-eval-opt.plan.md §4.1 / conv-optimize.plan.md §4.6）：agent 原先
 * 每局都 `spawn` 一个新 bun —— a95/Termux 实测那一下 ~2.5s（而一局游戏本身才 ~1s），且把
 * agent 的事件循环占满（任务中 `/v1/status` 首轮应答被拖到 2.59s）。长驻后进程启动、JIT、
 * wasm 编译与权重解析只付一次；`export-goal-rollout` / `export-intent-rollout` 入池的预期收益
 * 是这两个模式 +15–19%。
 *
 * 硬要求：传进来的 `main(argv)` 必须是「一个任务一局、每局新建 World」的入口 —— 只有这样
 * serve 与一次性调用的产物才逐字节一致（各导出器的 serve 测试钉的就是这条）。
 *
 * 本函数是 `PERSIST_SERVE_ENTRIES`（sampler-agent.ts）里每个条目的前置条件：要进池的导出器
 * 调它，别各自抄一份 —— 协议漂移的代价是 agent 侧把 worker 判成失败后**静默回落**一次性
 * spawn（慢，但不错），只有状态接口上的延迟数字会变成唯一的线索。
 */
export function runServe(main: (argv: string[]) => void): void {
  let buf = ''
  const handle = (line: string): void => {
    const t = line.trim()
    if (!t) return
    let argv: string[]
    try {
      const parsed: unknown = JSON.parse(t)
      // 非数组一律当坏行：JSON.parse 对 `123`/`{}` 是成功的，放进去会让 main 拿不到 argv
      // 而**静默跑默认网格**（16 局）—— 报错比跑错便宜。
      if (!Array.isArray(parsed)) throw new Error('not-array')
      argv = parsed as string[]
    } catch {
      process.stdout.write('__SERVE_ERR__ bad-json\n')
      return
    }
    try {
      main(argv)
      process.stdout.write('__SERVE_OK__\n')
    } catch (e) {
      process.stdout.write(`__SERVE_ERR__ ${e instanceof Error ? e.message : String(e)}\n`)
    }
  }
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (c: string) => {
    buf += c
    let nl = buf.indexOf('\n')
    while (nl >= 0) {
      handle(buf.slice(0, nl))
      buf = buf.slice(nl + 1)
      nl = buf.indexOf('\n')
    }
  })
  process.stdin.on('end', () => {
    if (buf.trim()) handle(buf)
    process.exit(0)
  })
  process.stdout.write('__SERVE_READY__\n')
}
