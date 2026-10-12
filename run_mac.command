#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then echo "Python3가 없습니다. https://www.python.org/downloads/ 에서 설치하세요."; read -n1; exit 1; fi
echo "[1/2] 라이브러리 설치 중 (처음 한 번만 오래 걸려요)"
python3 -m pip install --quiet --upgrade -r requirements.txt
echo "[2/2] 스크리너 실행 중"
python3 screener.py "$@"
