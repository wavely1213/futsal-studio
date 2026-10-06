@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 풋살사관학교 스튜디오 설치
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo [안내] Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요.
  echo        설치 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행: 필요한 구성요소를 설치합니다. 몇 분 걸립니다...
  %PY% -m venv .venv || (pause & exit /b 1)
)
echo 구성요소를 확인하는 중...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo 설치 실패 & pause & exit /b 1)
start "" ".venv\Scripts\pythonw.exe" updater.py --launch
echo.
echo 완료! 앱 창이 열립니다. 다음부터는 바탕화면의 "풋살사관학교 스튜디오" 아이콘으로 실행하세요.
timeout /t 4 >nul
exit
