@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 풋살사관학교 스튜디오 설치
rem 쓸 Python 고르기: 3.13 → 3.12 → 3.11 → 3.14 → 3.10 중 64비트(x64) · 'py -3'(가장 새 것)은 휠이 없는 3.15·ARM64 를 고를 수 있음
set "PY="
where py >nul 2>nul && call :pick_py
if not defined PY (
  python setup_check.py python >nul 2>nul && set "PY=python"
)
if not defined PY goto :no_python
rem 구성요소 폴더(.venv)가 깨졌거나(만든 Python 을 지움) 맞지 않으면 옮겨 두고 새로 만듦 (지우지 않음: .venv.old)
if exist ".venv\Scripts\python.exe" (
  %PY% setup_check.py venv
  if errorlevel 1 call :move_venv
)
if not exist ".venv\Scripts\python.exe" (
  %PY% setup_check.py longpath || goto :fail
  echo 처음 실행: 필요한 구성요소를 설치합니다. 몇 분 걸립니다...
  %PY% -m venv .venv || goto :fail
)
echo 구성요소를 확인하는 중...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade "pip>=23.3" >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || (echo 설치 실패 & pause & exit /b 1)
".venv\Scripts\python.exe" setup_check.py vcredist || call :vcredist
start "" ".venv\Scripts\pythonw.exe" updater.py --launch
echo.
echo 완료! 앱 창이 열립니다. 다음부터는 바탕화면의 "풋살사관학교 스튜디오" 아이콘으로 실행하세요.
timeout /t 4 >nul
exit

:pick_py
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V setup_check.py python >nul 2>nul && set "PY=py -%%V"
  )
)
exit /b 0

:move_venv
set "OLD=.venv.old"
if exist "%OLD%" set "OLD=.venv.old-%RANDOM%"
move ".venv" "%OLD%" >nul && exit /b 0
echo [안내] 예전 구성요소 폴더 .venv 를 옮기지 못했습니다. 앱을 모두 닫은 뒤 이 파일을 다시 실행하세요.
pause
exit

:vcredist
echo 받아쓰기·누끼에 필요한 Microsoft Visual C++ 구성요소를 설치합니다. 관리자 확인 창이 뜨면 '예'를 눌러 주세요...
curl -L -s -o "%TEMP%\vc_redist.x64.exe" https://aka.ms/vs/17/release/vc_redist.x64.exe
if errorlevel 1 goto :vcredist_manual
"%TEMP%\vc_redist.x64.exe" /install /passive /norestart
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
if "%RC%"=="3010" exit /b 0
if "%RC%"=="1638" exit /b 0
:vcredist_manual
echo [안내] 자동으로 설치하지 못했습니다. 열리는 페이지에서 받은 파일을 실행해 설치한 뒤 앱을 다시 켜 주세요.
start "" https://aka.ms/vs/17/release/vc_redist.x64.exe
exit /b 0

:no_python
echo.
echo [안내] 이 PC에 맞는 Python 을 찾지 못했습니다.
echo        필요한 것: Python 3.10~3.14, 64비트(x64) · 권장: Python 3.13
echo        https://www.python.org/downloads/windows/ 에서 "Windows installer (64-bit)" 를 받아 설치하세요.
echo        설치 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요. 설치한 뒤 이 파일을 다시 실행하세요.
start "" https://www.python.org/downloads/windows/
pause
exit /b 1

:fail
echo 설치 실패
pause
exit /b 1
