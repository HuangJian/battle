#!/bin/sh
# 在 Termux(app uid) → proot-distro/ubuntu 容器里跑一条命令：phone-run.sh "<shell 命令>"
P=/data/data/com.termux/files/usr
PATH=$P/bin:/system/bin
export PATH
exec proot-distro login ubuntu -- bash -lc "$1"
