@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 풋살사관학교 편집도우미
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
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo 설치 실패 & pause & exit /b 1)
echo 브라우저에서 편집도우미가 열립니다. 이 창을 닫으면 프로그램이 종료됩니다.
".venv\Scripts\python.exe" app.py
pause
