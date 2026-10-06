#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[안내] Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요."
  open https://www.python.org/downloads/
  read -p "엔터를 누르면 닫힙니다"; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo "처음 실행: 필요한 구성요소를 설치합니다. 몇 분 걸립니다..."
  python3 -m venv .venv || { read -p "실패. 엔터"; exit 1; }
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { read -p "설치 실패. 엔터"; exit 1; }
echo "브라우저에서 편집도우미가 열립니다. 이 창을 닫으면 프로그램이 종료됩니다."
.venv/bin/python app.py
