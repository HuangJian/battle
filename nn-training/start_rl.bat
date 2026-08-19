@echo off
cd /d D:\github\battle2\nn-training
.venv\Scripts\python.exe train_rl.py ^
  --num-envs 4 ^
  --num-steps 2048 ^
  --num-updates 10000 ^
  --report-freq 10 ^
  --eval-freq 100 ^
  --eval-episodes 10 ^
  --save-freq 100 ^
  --difficulty hard ^
  --output-dir weights\rl
