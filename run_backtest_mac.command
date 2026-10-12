#!/bin/bash
cd "$(dirname "$0")"
python3 -m pip install --quiet --upgrade -r requirements.txt
echo "백테스트 실행 중 (처음엔 10~20분 걸릴 수 있어요)"
python3 backtest.py "$@"
