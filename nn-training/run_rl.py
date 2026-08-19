"""Wrapper to start RL training with file logging."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Log to file with line buffering
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tmp', 'rl_train.log')
log_file = open(LOG_PATH, 'w', buffering=1)  # line-buffered
sys.stdout = log_file
sys.stderr = log_file

from train_rl import main

if __name__ == "__main__":
    main()
