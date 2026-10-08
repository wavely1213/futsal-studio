@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 풋살사관학교 스튜디오 설치
rem 압축 파일 안에서 바로 더블클릭하면 이 파일만 임시 폴더로 풀려 나머지 파일이 없음
if not exist "app.py" goto :not_extracted
if not exist "requirements.txt" goto :not_extracted
if not exist "setup_check.py" goto :not_extracted
rem 쓸 Python 고르기: 3.13 → 3.12 → 3.11 → 3.14 → 3.10 중 64비트(x64) · 'py -3'(가장 새 것)은 휠이 없는 3.15·ARM64 를 고를 수 있음
rem 새 'Python 설치 관리자'(pymanager)는 없는 버전을 부르면 몰래 설치를 시작할 수 있음 → 고르는 동안은 끔 (설치는 아래에서 보이게)
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "PY="
set "PYMGR="
where pymanager >nul 2>nul && set "PYMGR=1"
call :pick_py
if not defined PY if defined PYMGR call :pymanager_install
if not defined PY goto :no_python
rem 임시 폴더·압축 프로그램이 푼 폴더에서 실행하면 나중에 지워져 앱이 사라짐
%PY% setup_check.py place || goto :stop
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
rem 설치 기록은 임시 폴더에 남기고, 실패하면 이유를 한국어 한 줄로 (setup_check.py pip)
set "PIPLOG=%TEMP%\futsal-studio-pip.log"
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt > "%PIPLOG%" 2>&1 || goto :pip_fail
".venv\Scripts\python.exe" setup_check.py vcredist || call :vcredist
start "" ".venv\Scripts\pythonw.exe" updater.py --launch
echo.
echo 완료! 앱 창이 열립니다. 다음부터는 바탕화면의 "풋살사관학교 스튜디오" 아이콘으로 실행하세요.
timeout /t 4 >nul
exit

:pick_py
where py >nul 2>nul || goto :pick_mgr
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V setup_check.py python >nul 2>nul && set "PY=py -%%V"
  )
)
:pick_mgr
rem 예전 'Python launcher' 가 함께 깔려 py 가 예전 것이면 설치 관리자로 넣은 Python 은 pymanager 로 찾음
if not defined PYMGR goto :pick_path
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PY (
    pymanager exec -V:%%V setup_check.py python >nul 2>nul && set "PY=pymanager exec -V:%%V"
  )
)
:pick_path
if not defined PY (
  python setup_check.py python >nul 2>nul && set "PY=python"
)
exit /b 0

:pymanager_install
echo.
echo 새 'Python 설치 관리자'가 있어요. 이 앱에 맞는 Python 3.14 를 설치합니다. 인터넷으로 받아서 몇 분 걸려요...
set "PYTHON_MANAGER_CONFIRM=false"
pymanager install -y 3.14
call :pick_py
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
echo [안내] 이 PC에 이 앱에 맞는 Python 이 없어요.
echo        필요한 것: Python 3.10~3.14, 64비트 x64 · 3.15 는 아직 받아쓰기 구성요소가 없어서 쓸 수 없어요.
where py >nul 2>nul && py -3 setup_check.py python
if defined PYMGR echo        'Python 설치 관리자'로 3.14 를 설치하지 못했어요. 인터넷 연결을 확인하거나 아래 파일로 설치하세요.
echo        1. 지금 열리는 주소에서 python-3.14.8-amd64.exe 가 받아져요. 받은 파일을 실행하세요.
echo        2. 첫 화면 아래 'Add python.exe to PATH' 를 체크하고 'Install Now' 를 누르세요.
echo        3. 설치가 끝나면 이 파일을 다시 실행하세요. 이미 깔린 다른 버전은 지우지 않아도 돼요.
echo        주소: https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe
start "" https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.exe
pause
exit /b 1

:not_extracted
echo.
echo [안내] 압축 파일 안에서 바로 실행했어요. 앱 파일이 이 폴더에 없어요.
echo        지금 폴더: %~dp0
echo        1. 받은 압축 파일을 오른쪽 클릭하고 '압축 풀기' 또는 '모두 압축 풀기'를 누르세요.
echo        2. C:\풋살스튜디오 처럼 짧은 곳에 푸세요.
echo        3. 푼 폴더 안의 '시작하기 (Windows).bat' 을 더블클릭하세요.
pause
exit /b 1

:pip_fail
echo.
".venv\Scripts\python.exe" setup_check.py pip "%PIPLOG%"
echo 설치 실패
pause
exit /b 1

:stop
pause
exit /b 1

:fail
echo 설치 실패
pause
exit /b 1
