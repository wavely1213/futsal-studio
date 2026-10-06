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
echo "구성요소를 확인하는 중..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { read -p "설치 실패. 엔터"; exit 1; }
nohup .venv/bin/python app.py >/dev/null 2>&1 &
echo ""
echo "완료! 앱 창이 열립니다. 다음부터는 응용 프로그램 폴더(~/Applications)의 '풋살사관학교 스튜디오'로 실행하세요."
echo "이 창은 닫아도 됩니다."
