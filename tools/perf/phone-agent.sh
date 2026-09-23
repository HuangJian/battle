#!/bin/bash
# 容器内：phone-agent.sh <start|stop|log|key|ping> [workers] [port]
# 用途：在 Android 节点上拉起真正的 sampler-agent（长驻池），供 PC 侧驱动跑局。
cd /root/battle || exit 1
export PATH=/root/.bun/bin:$PATH
CMD=${1:-start}
WORKERS=${2:-8}
PORT=${3:-8443}
case "$CMD" in
  start)
    pkill -f "sampler-agent.ts --port $PORT" 2>/dev/null
    sleep 1
    nohup setsid bun tools/agent/sampler-agent.ts --port "$PORT" --workers "$WORKERS" \
      > /tmp/sampler-agent.log 2>&1 &
    sleep 8
    echo "--- 启动行 ---"; grep -E "listening on|authKey" /tmp/sampler-agent.log | head -3
    echo "--- authKey ---"; cat tools/agent/agent.auth 2>/dev/null
    echo "--- 自检 ---"; curl -s -m 5 -H "Authorization: Bearer $(cat tools/agent/agent.auth 2>/dev/null)" "http://127.0.0.1:$PORT/v1/ping" | head -c 300; echo
    ;;
  stop)  pkill -f "sampler-agent.ts --port $PORT"; echo stopped ;;
  log)   tail -"${2:-30}" /tmp/sampler-agent.log ;;
  key)   cat tools/agent/agent.auth ;;
  ping)  curl -s -m 5 -H "Authorization: Bearer $(cat tools/agent/agent.auth)" "http://127.0.0.1:$PORT/v1/ping"; echo ;;
esac
