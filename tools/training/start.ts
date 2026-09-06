#!/usr/bin/env bun
/**
 * start.ts — NN 训练/HUB 统一启动器（tools/training/ 主入口）。
 *
 * 完整取代：tools/hub-start.ts（hub 模式）+ nn-training/start-training.sh/.ps1
 * （train 模式）+ push 模式（HUB 推架构，DECISIONS §340 补充 4）。
 *
 * 用法：
 *   bun tools/training/start.ts hub <course> [--smoke-only] [--no-tunnel] [--kill]
 *   bun tools/training/start.ts push <course> [--smoke-only] [--with-hub] [--kill]
 *   bun tools/training/start.ts train [--script <name>.py] [args...] [--force]
 *       [--kill-previous] [--detach] [--torch-threads N]
 *
 * 三种模式（启动训练前都做轻量级冒烟测试）：
 *   hub   — Kaggle pull 全基建：self-node ∥ hub-server ∥ cloudflared → 冒烟门禁
 *           （基建 + rollout + 隧道/code.zip）→ TrainingLoop；--smoke-only 再加
 *           Kaggle 交互预演（真课程 --smoke job 穿隧道 echo 回显一整趟）。
 *   push  — HUB 推架构：本机只做 rollout 采集 + TrainingLoop(--ppo remote)，
 *           GPU 在 Kaggle 侧（nodes[].gpu_push）；--smoke-only 用本机伪 GPU 节点
 *           （remote_worker_serve echo）预演真推送链路；--with-hub 补齐 pull 基建。
 *   train — 本地 CPU 训练（torch/venv 经 bootstrap.py 委派），任何 .py 训练脚本。
 *
 * 变更检测（自动重启）：受管长跑进程（self-node / hub-server / TrainingLoop /
 * worker_server）由监督循环周期性检查"哨兵文件"（codehash-files.txt SSOT 清单 +
 * 各自入口源码）的 mtime/size——运行的代码更新后自动重启该进程应用最新代码。
 * 本地 train 模式为前台短命语义（一次一进程），不参与监督。
 *
 * 实现约定（沿袭 hub-start §339）：
 *   - 进程/端口/文件全走 Bun 原生 API，无 netstat/tasklist/ps 等平台分支；
 *   - 一切等待以"命令输出/健康探测"触发（waitUntil 轮询探测），无硬编码 sleep；
 *   - 相互独立的组件并行启动、并行检测（Promise.allSettled）；
 *   - detached + windowsHide：子进程脱离父进程 Job Object 存活且不弹控制台窗口。
 */

import { existsSync, readFileSync } from 'fs'
import path from 'path'
import { LOG_DIR, NN_TRAINING, REPO_ROOT } from './paths'
import { initLog, info, log, ok, warn, fail, RED, CYAN, GRAY, GREEN, NC } from './log'
import { loadConfig, printRecentCourses, validateCourseArg } from './config'
import { killPid, pidAlive, waitUntil, httpOk } from './net'
import { loadRegistry, saveComponent } from './registry'
import { stopAllManaged, spawnBg } from './proc'
import { createSupervisor, type ProcSupervisor } from './reload'
import { resolveVenvPython } from './venv'
import { monitorTouch } from './reload-touch'
import { pySentinels } from './sentinels'
import { containerSmoke, rlConfigSmoke, selfNodeSmoke, rolloutSmoke, summarizeSmoke } from './smoke'
import {
  hubServerHealthy,
  selfNodeHealthy,
  stepCloudflared,
  stepHubServer,
  stepKaggleRehearsal,
  stepNodesCheck,
  stepSelfNode,
  stepSmokeTest,
  stepTrainingLoop,
} from './hub'
import { runTrainMode, type TrainOptions } from './train'
import { pushSmokeEnv, startLocalWorkerServer, stopLocalWorkerServer } from './push'
import type { ProcSpec, RlConfig, StartMode } from './types'

// ────────────────────────── CLI ──────────────────────────

interface Cli {
  mode: StartMode | ''
  course: string
  smokeOnly: boolean
  doKill: boolean
  noTunnel: boolean
  withHub: boolean
  help: boolean
  train: TrainOptions
}

function usage(): void {
  console.log(`用法: bun tools/training/start.ts <hub|push|train> [args]

  hub <course> [--smoke-only] [--no-tunnel] [--kill]
      Kaggle pull 全基建 + TrainingLoop（原 tools/hub-start.ts）。
      <course>       课程名（位置参数或 --course <name>）
      --smoke-only   冒烟：基建 + rollout 冒烟 + Kaggle 交互预演（不跑真 PPO）
      --no-tunnel    显式跳过隧道（无 cloudflared 的机器；Kaggle 路径不验证）
      --kill         停止所有受管进程（无需课程）

  push <course> [--smoke-only] [--with-hub] [--kill]
      HUB 推架构（DECISIONS §340 补充 4）：GPU 在 Kaggle 侧（nodes[].gpu_push）。
      --smoke-only   本机伪 GPU 节点（remote_worker_serve echo）预演推送链路
      --with-hub     额外拉起 hub-server + cloudflared（pull 基建并存）
      --kill         停止所有受管进程

  train [--script <name>.py] [args...] [--force] [--kill-previous] [--detach] [--torch-threads N]
      本地 CPU 训练（原 nn-training/start-training.sh/.ps1）。
      --script       训练脚本（相对 nn-training/；缺省 train_loop.py；旧扁平名自动别名）
      其余参数原样透传给训练脚本。

  通用：
      变更检测：受管长跑进程（hub/push 模式）运行的代码更新后自动重启应用最新代码。
      冒烟：三种模式启动训练前都做轻量级冒烟测试（契约/回环/端到端按模式取子集）。`)
}

function parseCli(argv: string[]): Cli {
  const cli: Cli = {
    mode: '',
    course: '',
    smokeOnly: false,
    doKill: false,
    noTunnel: false,
    withHub: false,
    help: false,
    train: {
      script: 'train_loop.py',
      scriptArgs: [],
      force: false,
      killPrevious: false,
      echo: false,
      check: false,
      detach: false,
      torchThreads: 0,
    },
  }
  // train 子命令的透传参数从 --script 之后开始收集（未知选项透传给训练脚本）。
  let inTrainPass = false
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i] ?? ''
    if (!inTrainPass) {
      switch (a) {
        case 'hub':
        case 'push':
        case 'train':
          if (cli.mode) {
            console.error(`模式重复指定: ${cli.mode} + ${a}`)
            process.exit(1)
          }
          cli.mode = a
          if (a === 'train') inTrainPass = true // train 之后进入透传解析
          continue
        case '--course': {
          const v = argv[++i] ?? ''
          if (cli.course) {
            console.error(`课程重复指定: ${cli.course} + ${v}（位置参数与 --course 只能二选一）`)
            process.exit(1)
          }
          cli.course = v
          continue
        }
        case '--smoke-only':
          cli.smokeOnly = true
          continue
        case '--kill':
          cli.doKill = true
          continue
        case '--no-tunnel':
          cli.noTunnel = true
          continue
        case '--with-hub':
          cli.withHub = true
          continue
        case '--help':
        case '-h':
          cli.help = true
          continue
      }
    }
    // train 透传段：已知启动器选项 + 其余进 scriptArgs
    if (cli.mode === 'train') {
      switch (a) {
        case '--script': {
          const v = argv[++i] ?? ''
          if (!v) {
            console.error('ERROR: --script requires a <name>.py')
            process.exit(2)
          }
          cli.train.script = v
          continue
        }
        case '--force':
          cli.train.force = true
          continue
        case '--kill-previous':
        case '--killprevious':
          cli.train.killPrevious = true
          continue
        case '--echo':
          cli.train.echo = true
          continue
        case '--check':
          cli.train.check = true
          continue
        case '--detach':
          cli.train.detach = true
          continue
        case '--torch-threads':
        case '--torch_threads': {
          const v = Number(argv[++i])
          if (Number.isFinite(v)) cli.train.torchThreads = v
          continue
        }
        default:
          cli.train.scriptArgs.push(a)
          continue
      }
    }
    // hub/push 的位置参数 = 课程名
    if (a.startsWith('-')) {
      console.error(`未知参数: ${a}`)
      process.exit(1)
    }
    if (cli.course) {
      console.error(`课程重复指定: --course ${cli.course} + 位置参数 ${a}`)
      process.exit(1)
    }
    cli.course = a
  }
  return cli
}

// ────────────────────────── 代理环境整形（回环流量永远直连） ──────────────────────────

function shapeProxyEnv(): boolean {
  const hadProxy = !!(
    process.env.HTTP_PROXY ||
    process.env.HTTPS_PROXY ||
    process.env.http_proxy ||
    process.env.https_proxy
  )
  if (hadProxy) {
    const cur = (process.env.NO_PROXY || process.env.no_proxy || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const merged = [...new Set([...cur, 'localhost', '127.0.0.1', '.trycloudflare.com'])].join(',')
    process.env.NO_PROXY = merged
    process.env.no_proxy = merged
  }
  return hadProxy
}

// ────────────────────────── 冒烟（模式前置门禁） ──────────────────────────

/** 轻量冒烟（所有模式共用前半）：rl-config 契约 + BCV2 容器回环。硬失败即不启动。 */
function baseSmokeOrExit(cfg: RlConfig | null): void {
  log('运行启动冒烟测试...')
  const items = [containerSmoke()]
  if (cfg) items.push(rlConfigSmoke(cfg))
  if (!summarizeSmoke(items)) {
    fail('启动冒烟未通过——放弃启动')
    process.exit(1)
  }
  ok('启动冒烟通过')
}

// ────────────────────────── 变更检测监督 ──────────────────────────

/** 监督器单例（hub/push 模式创建；restart 回调 = 按原 spec 重启并回灌账本）。 */
function makeSupervisor(): ProcSupervisor {
  const restart = async (spec: ProcSpec, oldPid: number): Promise<number> => {
    await killPid(oldPid)
    const r = spawnBg(spec.cmd, { cwd: spec.cwd, env: spec.env, log: spec.log })
    saveComponent(spec.key, { pid: r.pid, entry: spec.sentinels[1], log: spec.log })
    monitorTouch()
    const ready = await waitUntil(spec.healthy, 45000, 500)
    if (!ready) warn(`${spec.name} 重启后 45s 未就绪（进程 ${r.pid}，继续观察）`)
    else ok(`${spec.name} 已应用最新代码 (PID ${r.pid})`)
    return r.pid
  }
  return createSupervisor(restart, { intervalMs: 5000 })
}

// ────────────────────────── hub / push 共用全流程 ──────────────────────────

interface HubRun {
  cfg: RlConfig
  course: string
  weightsPath: string
  jobRoot: string
  jsonlPath: string
  venv: { python: string; sitePackages: string }
}

function hubRunFrom(course: string, cfg: RlConfig): HubRun {
  const trajDir = path.join(REPO_ROOT, 'tmp', course)
  return {
    cfg,
    course,
    weightsPath: path.join(trajDir, 'weights.json'),
    jobRoot: path.join(trajDir, 'remote-jobs'),
    jsonlPath: path.join(trajDir, 'training_log.jsonl'),
    venv: resolveVenvPython(),
  }
}

/** 基础设施三件套并行启动（self-node ∥ hub-server ∥ cloudflared）。 */
async function startInfra(run: HubRun, noTunnel: boolean): Promise<string> {
  const results = await Promise.allSettled([
    stepSelfNode(run.cfg),
    stepHubServer(run.cfg, run.jobRoot, run.jsonlPath),
    stepCloudflared(run.cfg, noTunnel),
  ])
  if (results[1].status === 'rejected') throw results[1].reason
  if (results[2].status === 'rejected') throw results[2].reason
  if (results[0].status === 'rejected') warn('self-node 未就绪，local 模式兜底')
  return noTunnel ? '' : results[2].status === 'fulfilled' ? (results[2].value as string) : ''
}

/** 检测与冒烟并行（节点检测 ∥ 基建冒烟 ∥ rollout 冒烟）。 */
async function checksPhase(run: HubRun, cfUrl: string | null, noTunnel: boolean): Promise<void> {
  const [nodes, smoke, rollout] = await Promise.allSettled([
    stepNodesCheck(run.cfg),
    stepSmokeTest(run.cfg, cfUrl, noTunnel),
    rolloutSmoke(run.cfg, run.weightsPath),
  ])
  // 隧道/code.zip/self-rollout 是 Kaggle 接入与本地采集的硬门——失败即终止启动
  if (smoke.status === 'rejected') throw smoke.reason
  if (rollout.status === 'rejected') throw rollout.reason
  if (nodes.status === 'rejected') warn('节点检测失败（继续，local 模式兜底）')
  else if (rollout.status === 'fulfilled') {
    const r = rollout.value
    if (r.passed) ok(`rollout 冒烟: ${r.detail ?? '全通过'}`)
    else warn(`rollout 冒烟: ${r.detail ?? '部分失败'}`)
    if (!r.passed && r.fatal) throw new Error(r.detail ?? 'rollout 冒烟未通过（self 节点）')
  }
}

/** 最终体检报告（并行）+ 退出码。 */
async function finalReport(
  run: HubRun,
  cfUrl: string | null,
  noTunnel: boolean,
  extra: { label: string; ok: boolean }[],
): Promise<void> {
  const [hubOk, selfOk, tlAlive, tunnelOk] = await Promise.all([
    hubServerHealthy(run.cfg),
    selfNodeHealthy(run.cfg),
    Promise.resolve(pidAlive(loadRegistry().trainingLoop?.pid)),
    noTunnel || !cfUrl
      ? Promise.resolve(false)
      : httpOk(`${cfUrl}/ping`, run.cfg.rl.remote_token, 10000),
  ])
  console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
  console.log(`${CYAN}║              启动报告                          ║${NC}`)
  console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}\n`)
  if (hubOk) ok(`hub-server      http://127.0.0.1:${run.cfg.rl.hub_port}`)
  else fail('hub-server     未运行')
  if (selfOk) ok(`self-node       http://127.0.0.1:${run.cfg.rl.agent_port}`)
  else fail('self-node      未运行')
  if (noTunnel) info('cloudflared     未启用（--no-tunnel，Kaggle 路径未验证）')
  else if (cfUrl && tunnelOk) ok(`cloudflared     ${cfUrl}`)
  else fail('cloudflared     隧道不可达——Kaggle 无法连接')
  if (tlAlive) ok(`TrainingLoop    course=${run.course}`)
  else fail('TrainingLoop   未运行')
  for (const e of extra) {
    if (e.ok) ok(e.label)
    else fail(e.label)
  }

  let allOk = hubOk && selfOk && tlAlive && (noTunnel || (cfUrl !== null && tunnelOk))
  for (const e of extra) if (!e.ok) allOk = false
  process.exitCode = allOk ? 0 : 1

  if (cfUrl) {
    console.log(`\n${GREEN}📋 Kaggle 接入指引:${NC}`)
    console.log(`  1. HUB_URL = "${cfUrl}"（已写入 rl-config.json）`)
    console.log(`  2. TOKEN   = "${run.cfg.rl.remote_token}"（rl-config.json 的 rl.remote_token）`)
    console.log(`  3. 打开 Kaggle notebook: nn-training/ipynb/ 下的对应课程 notebook`)
    console.log(
      `  4. worker 一行命令：python -m remote_worker --poll <HUB_URL> --token <TOKEN> --device cuda`,
    )
  }
  console.log(`\n${GRAY}日志文件:${NC}`)
  console.log(`  hub-server:    nn-training/tmp/hub-server.out`)
  console.log(`  self-node:     nn-training/tmp/sampler-agent.log`)
  console.log(`  TrainingLoop:  nn-training/tmp/${run.course}/training-loop.log`)
  console.log(`  停止全部:      bun tools/training/start.ts hub --kill`)
  console.log('')
}

// ────────────────────────── 模式实现 ──────────────────────────

async function runHub(cli: Cli, sup: ProcSupervisor): Promise<void> {
  const cfg = loadConfig()
  const run = hubRunFrom(cli.course, cfg)

  if (cli.doKill) {
    await stopAllManaged()
    return
  }

  if (cli.smokeOnly) {
    // 阶段 1：基建三件套并行；隧道失败 = 冒烟不通过（硬门）
    const cfUrl = await startInfra(run, cli.noTunnel)
    // 阶段 2：节点检测 + 基建冒烟 + rollout 冒烟并行
    await checksPhase(run, cfUrl, cli.noTunnel)
    // 阶段 3：本机伪 GPU 节点（与真 Kaggle 同一套 run_job 代码路径；echo 不跑 PPO）
    const { pushUrl, servePid } = await startLocalWorkerServer({
      course: run.course,
      cfgToken: cfg.rl.remote_token,
      hubPort: cfg.rl.hub_port,
      venv: run.venv,
    })
    // 阶段 4：Kaggle 交互预演——真课程 TrainingLoop（--smoke）发布真 job 并推送
    const started = await stepTrainingLoop(cfg, {
      course: run.course,
      weightsPath: run.weightsPath,
      jobRoot: run.jobRoot,
      jsonlPath: run.jsonlPath,
      smoke: true,
      pushNodeUrl: pushUrl,
      venv: run.venv,
    })
    sup.watch(trainingSpec(run, pushUrl), loadRegistry().trainingLoop?.pid ?? 0)
    let rehearsalOk = true
    if (!started) {
      warn('TrainingLoop 已在运行（可能是真训练）——跳过 Kaggle 预演，以免干扰在途 job')
    } else {
      const tlPid = loadRegistry().trainingLoop?.pid ?? 0
      try {
        await stepKaggleRehearsal(run.course, tlPid)
      } catch (e) {
        rehearsalOk = false
        fail(`Kaggle 交互预演未通过: ${(e as Error).message}`)
        if (pidAlive(tlPid)) {
          await killPid(tlPid)
          warn('已停止冒烟用 TrainingLoop（--smoke 进程没有 echo 结果会一直等待）')
        }
      }
    }
    await stopLocalWorkerServer(servePid)
    // 冒烟总结（任何硬门失败都以非零码退出）
    const hubOk = await hubServerHealthy(cfg)
    const selfOk = await selfNodeHealthy(cfg)
    console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
    console.log(`${CYAN}║                 冒烟总结                      ║${NC}`)
    console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}\n`)
    if (hubOk) ok(`hub-server    http://127.0.0.1:${cfg.rl.hub_port}`)
    else fail('hub-server 未运行')
    if (selfOk) ok(`self-node     http://127.0.0.1:${cfg.rl.agent_port}`)
    else fail('self-node 未运行')
    if (cli.noTunnel) info('cloudflared   未启用（--no-tunnel）')
    else if (cfUrl) ok(`cloudflared   ${cfUrl}`)
    else fail('cloudflared 未建立')
    if (rehearsalOk) ok('Kaggle 交互预演全通过（发布→推送→echo→落位→作废）')
    else fail('Kaggle 交互预演未通过——见上方日志')
    process.exitCode = hubOk && selfOk && rehearsalOk ? 0 : 1
    if (cfUrl) {
      console.log(`\n${GREEN}📋 Kaggle 粘贴用:${NC}`)
      console.log(`  HUB_URL = "${cfUrl}"`)
      console.log(`  TOKEN   = "${cfg.rl.remote_token}"`)
    }
    console.log(
      `\n${GRAY}下一步：Kaggle notebook 填入 HUB_URL 接入；真训练直接跑全流程（bun tools/training/start.ts hub ${run.course}）${NC}\n`,
    )
    return
  }

  // ── 全流程 ──
  log('── 阶段 1/3: 基础设施（self-node ∥ hub-server ∥ cloudflared 并行）──')
  const cfUrl = await startInfra(run, cli.noTunnel)

  log('── 阶段 2/3: 检测与冒烟（节点检测 ∥ 基建冒烟 ∥ rollout 冒烟并行）──')
  await checksPhase(run, cfUrl, cli.noTunnel)

  log('── 阶段 3/3: TrainingLoop ──')
  preflightRunRlLock()
  const started = await stepTrainingLoop(cfg, {
    course: run.course,
    weightsPath: run.weightsPath,
    jobRoot: run.jobRoot,
    jsonlPath: run.jsonlPath,
    smoke: false,
    venv: run.venv,
  })
  if (started) {
    sup.watch(trainingSpec(run), loadRegistry().trainingLoop?.pid ?? 0)
  }

  await finalReport(run, cfUrl, cli.noTunnel, [])
}

/** TrainingLoop 的 ProcSpec（监督重启用；pushNodeUrl 仅冒烟注入）。 */
function trainingSpec(run: HubRun, pushNodeUrl?: string): ProcSpec {
  return {
    key: 'trainingLoop',
    name: 'TrainingLoop',
    cmd: [
      run.venv.python,
      '-u',
      path.join(NN_TRAINING, 'run_rl.py'),
      '--course',
      run.course,
      '--ppo',
      'remote',
    ],
    env: {
      PYTHONPATH: `${run.venv.sitePackages}${path.delimiter}${NN_TRAINING}`,
      ...(pushNodeUrl ? pushSmokeEnv(pushNodeUrl) : {}),
    },
    log: path.join(LOG_DIR, run.course, 'training-loop.log'),
    healthy: async () => pidAlive(loadRegistry().trainingLoop?.pid),
    sentinels: pySentinels('nn-training/run_rl.py'),
  }
}

/** run_rl 单实例锁预检（fail fast）：锁持有人存活时 TrainingLoop 起来也只会
 *  立即拒启（run_rl.py 自己的锁）。这里提前响亮报错并提示 --force 语义，避免
 *  预演/真训练把 180s 预演窗口空烧在"进程活着但没干活"上。 */
function preflightRunRlLock(): void {
  const lockPath = path.join(NN_TRAINING, '.run_rl.lock')
  if (!existsSync(lockPath)) return
  try {
    const holder = Number.parseInt((readFileSync(lockPath, 'utf-8').split('|')[0] ?? '').trim(), 10)
    if (Number.isInteger(holder) && holder > 0 && pidAlive(holder)) {
      fail(
        `run_rl 锁被 PID ${holder} 持有（nn-training/.run_rl.lock）——TrainingLoop 将拒绝启动。` +
          `先停掉在跑的训练（或删除锁文件），再重跑本命令。`,
      )
      process.exit(1)
    }
  } catch {
    /* unreadable — let run_rl.py handle */
  }
}

async function runPush(cli: Cli, sup: ProcSupervisor): Promise<void> {
  const cfg = loadConfig()
  const run = hubRunFrom(cli.course, cfg)

  if (cli.doKill) {
    await stopAllManaged()
    return
  }

  preflightRunRlLock()
  log('── push 模式（HUB 推架构；GPU 节点 = rl-config nodes[].gpu_push）──')
  const enabled = cfg.nodes.filter((n) => n.enabled)
  const gpuPush = enabled.filter((n) => n.gpu_push)
  if (gpuPush.length === 0 && !cli.smokeOnly) {
    warn(
      '无 enabled 的 gpu_push 节点——真训练将无法推送（确认 Kaggle 侧 worker_server 已起且 URL 已贴入 rl-config）',
    )
  } else {
    for (const n of gpuPush) info(`GPU push 节点: ${n.id} → ${n.url}`)
  }

  // 可选 pull 基建（--with-hub）
  let cfUrl: string | null = null
  if (cli.withHub) {
    cfUrl = await startInfra(run, cli.noTunnel)
    await checksPhase(run, cfUrl, cli.noTunnel)
  } else {
    // push 最小基建：self-node（采集底线）
    await stepSelfNode(cfg)
  }
  const selfOk = await selfNodeSmoke(cfg)
  if (!selfOk.passed && selfOk.fatal) {
    fail('self-node 冒烟未通过——放弃启动')
    process.exit(1)
  }

  if (cli.smokeOnly) {
    // 本机伪 GPU 节点 + REMOTE_PUSH_NODE 注入预演（DECISIONS §340 补充 4）
    const { pushUrl, servePid } = await startLocalWorkerServer({
      course: run.course,
      cfgToken: cfg.rl.remote_token,
      hubPort: cfg.rl.hub_port,
      venv: run.venv,
    })
    const started = await stepTrainingLoop(cfg, {
      course: run.course,
      weightsPath: run.weightsPath,
      jobRoot: run.jobRoot,
      jsonlPath: run.jsonlPath,
      smoke: true,
      pushNodeUrl: pushUrl,
      venv: run.venv,
    })
    sup.watch(trainingSpec(run, pushUrl), loadRegistry().trainingLoop?.pid ?? 0)
    let rehearsalOk = true
    if (!started) {
      warn('TrainingLoop 已在运行——跳过预演，以免干扰在途 job')
    } else {
      const tlPid = loadRegistry().trainingLoop?.pid ?? 0
      try {
        await stepKaggleRehearsal(run.course, tlPid)
      } catch (e) {
        rehearsalOk = false
        fail(`推送链路预演未通过: ${(e as Error).message}`)
        if (pidAlive(tlPid)) {
          await killPid(tlPid)
          warn('已停止冒烟用 TrainingLoop')
        }
      }
    }
    await stopLocalWorkerServer(servePid)
    if (rehearsalOk) ok('推送链路预演全通过（发布→推送→echo→落位→作废）')
    else fail('推送链路预演未通过——见上方日志')
    process.exitCode = rehearsalOk ? 0 : 1
    return
  }

  // 全流程：TrainingLoop（GPU 节点来自 rl-config gpu_push）
  const started = await stepTrainingLoop(cfg, {
    course: run.course,
    weightsPath: run.weightsPath,
    jobRoot: run.jobRoot,
    jsonlPath: run.jsonlPath,
    smoke: false,
    venv: run.venv,
  })
  if (started) {
    sup.watch(trainingSpec(run), loadRegistry().trainingLoop?.pid ?? 0)
  }
  ok(`TrainingLoop 已按 push 模式启动 (course=${run.course})`)
  info(
    `Kaggle 侧接续：notebook 起 remote_worker_serve + cloudflared，URL 贴 rl-config nodes（gpu_push: true）`,
  )
  process.exitCode = 0
}

function runTrain(cli: Cli): void {
  // 前置：课程类参数对 train 无意义；直接进启动器（冒烟在 launchTraining 内做）
  const code = runTrainMode(cli.train)
  process.exitCode = code
}

// ────────────────────────── 主流程 ──────────────────────────

async function main(): Promise<void> {
  const cli = parseCli(process.argv.slice(2))
  if (cli.help || !cli.mode) {
    if (!cli.mode && !cli.help) {
      // 未指定模式：列最近课程辅助选择
      printRecentCourses()
    }
    usage()
    process.exit(cli.help ? 0 : 1)
  }

  const hadProxy = shapeProxyEnv()

  if (cli.mode === 'train') {
    initLog('train')
    if (hadProxy) info('检测到代理环境变量——已追加 NO_PROXY 直连回环')
    runTrain(cli)
    return
  }

  // hub/push：课程必填 + 快速失败校验
  if (!cli.course && !cli.doKill) {
    console.error(
      `\n必须指定课程（位置参数或 --course <name>，如: bun tools/training/start.ts ${cli.mode} p4-onset）。`,
    )
    printRecentCourses()
    process.exit(1)
  }
  if (cli.course) validateCourseArg(cli.course)

  initLog(`${cli.mode}-${cli.course || 'noclass'}`)
  if (hadProxy) {
    info(
      '检测到代理环境变量（HTTP(S)_PROXY）——回环已追加 NO_PROXY 直连' +
        '（localhost/127.0.0.1/.trycloudflare.com），防探测与预演被代理规则引入假阴性',
    )
  }

  console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
  console.log(`${CYAN}║  Battle City — training 统一启动器（${cli.mode.padEnd(5)}）  ║${NC}`)
  console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}\n`)

  // 基础冒烟（配置契约 + 容器回环）——在任何基础设施启动之前
  let cfg: RlConfig | null = null
  try {
    cfg = loadConfig()
  } catch {
    /* 无配置：train 类模式才允许 */
  }
  baseSmokeOrExit(cfg)
  if (!cfg) {
    fail('rl-config.json 不可读——hub/push 模式必需')
    process.exit(1)
  }

  const sup = makeSupervisor()
  log(`变更检测监督已启用（哨兵: codehash-files.txt + 各进程入口源码；轮询 5s）`)

  if (cli.mode === 'hub') await runHub(cli, sup)
  else if (cli.mode === 'push') await runPush(cli, sup)

  // --kill 是同步运维流程：完成即退，不留后台监督（受管进程已停止，无需检测）。
  if (cli.doKill) {
    sup.stop()
    return
  }

  // 主流程返回后监督循环持续运行（长跑模式）：哨兵变更 → 自动重启对应进程。
  log('启动器主流程完成——变更检测监督持续运行（哨兵变更即自动重启对应进程；Ctrl-C 退出监督）')
}

main().catch((e) => {
  console.error(`\n${RED}${e.message || e}${NC}`)
  process.exit(1)
})
