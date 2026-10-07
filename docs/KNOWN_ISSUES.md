# KNOWN_ISSUES.md — 알려진 이슈

> **목적**: 미해결 버그, 기술 부채, 임시방편(workaround), 제약사항의 저장소.
> AI는 (a) 작업 시작 전 관련 이슈가 있는지 훑고, (b) 스코프 밖 문제를 발견하면
> 고치는 대신 여기에 기록한다.
>
> **작성 규칙**: 해결된 이슈는 삭제하지 않고 상태를 `해결`로 바꾸고 해결 방법을 한 줄 남긴다
> (같은 문제 재발 시 참고). 분기마다 해결 항목을 하단 아카이브로 이동.

---

## 템플릿

```markdown
## I-001 | YYYY-MM-DD | 이슈 제목
- **상태**: 열림 / 해결(YYYY-MM-DD) / 보류(이유)
- **심각도**: 높음(데이터·보안·장애) / 중간(기능 결함) / 낮음(불편·부채)
- **증상/내용**: 무엇이 문제인가 (재현 방법 포함, 1~3줄)
- **위치**: 관련 파일·모듈
- **임시방편**: 현재 어떻게 회피 중인가 (없으면 생략)
- **해결 방향**: 알고 있다면 (없으면 생략)
```

---

## 열린 이슈

<!-- 최신 항목을 위에 추가. 심각도 '높음'은 발견 즉시 소유자에게 별도 보고 -->

## I-044 | 2026-10-07 | Windows 대비 묶음(I-034~I-043): Windows 실기 미검증 · 남은 것
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 아래는 Linux 에서 흉내로만 확인했다. (1) Job Object(`core.track`)로 앱이 꺼질 때 ffmpeg·pip·claude 가 같이 꺼지는지 · yt-dlp 가 안에서 띄우는 합치기 ffmpeg 는 묶음 밖이라 남을 수 있음. (2) 포트: Windows 에서 SO_REUSEADDR 를 끈 뒤 업데이트 재시작이 바로 같은 포트를 잡는지, 예약 포트(WinError 10013)에서 8766~ 로 켜지는지. (3) 창 닫기 확인(`confirm`)이 WebView2 에서 뜨는지 · pywebview `private_mode=False` 로 설정(localStorage)이 남는지. (4) `studio-error.log`·`faulthandler` 가 pythonw 에서 쓰이는지. (5) bat: `py -3.13` 고르기·`.venv.old` 옮기기·`curl` 로 Visual C++ 설치(관리자 확인 창)·긴 경로 안내. (6) 바로가기 COM·AppUserModelID (I-015). (7) 미뤄 둔 구성요소 설치(`.req_pending`) 동안 창이 늦게 뜨는 시간. (8) 백신이 완성본을 60초보다 오래 잡는 PC.
  - 남은 것: 받다 끊긴 yt-dlp 중간 파일(`.f137.mp4` 등)은 보관함에서 숨기기만 하고 지우지 않는다(파일 지우기는 승인 사항 · 같은 영상을 다시 받으면 yt-dlp 가 이어 씀). 클로드 대답 시간 제한은 벽시계 기준이지만 Windows 의 `time.monotonic` 도 절전 시간을 세므로 바꾸지 않았다. Modern Standby 노트북에서 `ES_SYSTEM_REQUIRED` 만으로 잠들지 않는지 모른다.
- **위치**: `core.py` `track`·`popen`·`keep_awake`, `app.py` `_bind`·`_confirm_close`·`_error_log`·`_start_webview`, `winlink.py`, `시작하기 (Windows).bat`·`setup_check.py`, `updater.py` `_deferred_pip`·`_launch_lock`
- **해결 방향**: 소유자 PC 에서: 내보내기 도중 창 닫기(작업 관리자에 ffmpeg 가 남는지), 아이콘 빠르게 두 번 누르기, 다른 프로그램으로 8765 를 잡은 채 켜기, bat 을 Python 3.13 으로 다시 실행, 바로가기로 켠 창을 작업 표시줄에 고정.

## I-033 | 2026-10-07 | 한 줄 자막: 글자 폭 어림 · 끊는 곳 품질 · Windows 실기 미검증
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: (1) 한 줄 자막이 화면 폭(90%)을 넘는지는 실제 글꼴을 재지 않고 글자 종류별 폭(한글 0.9em 등)으로 어림한다 (`editor.cap_fit`·`editor.html` `capFit`). 어림이 실제보다 작으면 WebView2·libass 에서 한 줄이 두 줄로 감길 수 있다. 0.7배보다 더 줄여야 하는 긴 자막(예전 자막)은 줄이지 않고 예전처럼 줄을 바꾼다. (2) 단어 시각이 없는 예전 받아쓰기는 시간을 글자 수대로 나누므로 자막이 말보다 조금 빠르거나 늦을 수 있다 (조용한 곳이 있으면 거기에 맞춤). (3) 끊는 곳은 낱말 끝 모양으로만 고른다(형태소 분석 없음). 말이 아주 느리면(낱말당 0.8초 넘게) 2.2초 한도 때문에 '들어야 / 돼요'처럼 붙여 읽는 말 사이에서 끊기는 일이 남는다. '~는'은 꾸미는 말인지 주제어인지 구분하지 않고 '하는·있는' 같은 몇 가지만 꾸미는 말로 본다. (4) 아주 빠른 말(1초에 30글자 넘게)은 0.5초 넘게 보이도록 합치면 한도의 1.5배를 넘을 수 있어 그대로 짧게 스칠 수 있다. (5) 글을 고쳐 낱말 수가 바뀐 가로 자막은 쇼츠 편집본에서 나누지 않고(글자 줄이기·줄바꿈) — '자막 한 줄씩 나누기'를 다시 누르면 다시 나뉜다.
- **위치**: `captions.chunk`·`split_text`·`even_words`, `editor.cap_fit`·`oneline_captions`, `editor.html` `capFit`·`capOneLine`
- **해결 방향**: Windows 에서 큰 글씨 쇼츠를 열어 미리보기·내보낸 영상 자막이 한 줄인지 확인 (I-009 와 함께). 예전 받아쓰기는 '편집점 다시 찾기'로 단어 시각을 받으면 정확해진다.

## I-032 | 2026-10-07 | 학습용 영상: Windows 실기·실제 YouTube 받기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 학습용 영상 받기는 가짜 yt-dlp 로만 시험했다(실제 채널 인기 영상 받기·방향 A/B/C 한 번에 받기는 개발 PC 에서 YouTube 가 막아 확인 못 함). 추천 영상 중 지워졌거나 비공개가 된 것은 그 영상만 실패로 남는다. 받는 중 앱이 꺼지면 `refs/_받는 중/`에 다 받은 파일은 다음 목록에서 채널 폴더로 옮겨지지만(채널 정보가 없으면 '채널 모름' 칸), 받다 만 `.part` 파일은 남는다. 보관함에서 옮긴 영상의 썸네일 문서는 제자리에 남는다('보관함으로'로 되돌리면 다시 쓰임). 편집실 프로젝트에서 쓰는 영상은 옮기지 않지만, 썸네일 문서에서만 쓰는 영상은 옮겨진다. '<채널명> 스타일'을 자동으로 다시 배우면 그 스타일에 저장된 Claude 판단(`plan.ai`)은 사라진다('다시 배우기'와 같음). 추천 채널 목록·구독자 수는 2026-10-07 조사 값 그대로다.
- **Windows 미검증**: 편집실·탐색기·백신이 잡고 있는 영상·분석 폴더 옮기기·지우기·되돌리기(PermissionError 다시 시도는 흉내로만 시험 · 막 받은 큰 영상은 최대 60초 기다림), 폴더째 `os.replace`, 채널 이름으로 만든 폴더(예약 이름·끝 점·쪼개진 자모), 한글 사용자 폴더의 `refs` 열기.
- **위치**: `refs.py`, `core.download`, `ui.html` 스타일 배우기·보관함
- **해결 방향**: 소유자 PC 에서 채널 하나(인기 3개)와 방향 하나를 실제로 받아 보고, 편집실을 연 채로 옮기기를 눌러 안내가 나오는지 확인.

## I-031 | 2026-10-07 | 영상 기획 분석: 실제 유튜버 영상 판단 품질·Windows 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 판단 규칙은 합성 정답 영상(티저·타이틀·예능 자막·정지·슬로 리플레이)과 단위 시험으로만 확인했다. 슛포러브·도블락 같은 실제 레퍼런스에서의 판단 품질(장르·인트로·자막 종류·재미 요소)은 아직 모른다. 화자 수(MFCC 군집)와 장소(초록 바닥 비율 → '잔디 구장'만 말함, 야외/실내는 판단 안 함)는 어림이다(합성 목소리 2명은 가려냈음). 티저·리플레이는 장면 전환(컷) 기록에 기대므로 컷 찾기가 놓친 전환(빠른 디졸브 등)에서는 티저를 놓칠 수 있다(안전한 쪽). 같은 자리로 자주 돌아오는 레슨(본편 중간에 처음과 같은 설명 자리)은 그 화면이 영상의 8% 넘게 나오면 '늘 보이는 화면'으로 빼지만, 그보다 적으면 티저로 잘못 볼 수 있다. 장르 낱말·훅 낱말 사전과 강조 자막 낱말은 합성 문장으로만 맞췄다. 클로드 판단 화면 예시(`plan_shots/p7`)는 70초 합성 영상으로 만든 것이라 실제 슛포러브 분석이 아니다. OCR은 굵은 글꼴의 한 글자를 다르게 읽기도 한다(예: '쾅'→'광'). 웃음·효과음은 YAMNet이 없으면 '모름'·어림이다.
- **Windows 미검증**: Claude CLI 찾기(`PATH`·`%USERPROFILE%\.local\bin\claude.exe`·npm `claude.cmd`), 보이는 PowerShell 설치 창·로그인 창(`CREATE_NEW_CONSOLE`), `taskkill /T /F`로 프로세스 트리 끄기, `cmd.exe /c claude.cmd`의 인자 따옴표(빈 인자는 빼고 Read만), 한글 사용자 폴더에서 OCR·YAMNet 모델 불러오기(바이트로 불러옴), 저사양 노트북에서 OCR 상한 자동 낮춤.
- **위치**: `plan.py`, `avmodels.py`, `claude_cli.py`, `ui.html` 스타일 카드
- **해결 방향**: 소유자 PC에서 실제 레퍼런스 2~3개로 배우고 판단 문장을 확인 → 규칙 문턱(`plan.py` 맨 위 상수) 조정. 남은 가편집 반영(인사 줄이기, 펀치라인 줌, 효과음, 슬로 리플레이, 정지 화면, 밈, 속마음 자막 생성)은 화면에 '직접 해 보세요'로만 안내한다.

> I-001~I-018은 문서 체계를 도입하면서(2026-10-06) 한꺼번에 기록했다. 심각도가 높은 것이 위에 있다. I-019~I-023은 같은 날 문서 검토에서 더했다.
> "실기 미검증"은 개발 PC(Linux)의 단위·e2e 시험은 통과했지만 실제 Windows PC에서는 아직 확인하지 않았다는 뜻이다.

## I-029 | 2026-10-07 | 예전 영상 출처: 인터넷 조회를 못 하면 제목 앞부분으로 짐작 · Windows 실기 미검증
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: v1.9.0 이전의 우리 채널 목록 기억(`channel_cache.json`)에는 영상 id 가 없다. 인터넷 조회(`source._lookup`)를 못 한 예전 영상은 제목 앞부분(기호·띄어쓰기 빼고 6글자 이상)이 그 기억의 제목과 같으면 '풋살사관학교 (추정)'으로 보인다. 같은 제목의 다른 채널 영상이 잘못 잡힐 수 있지만, 짐작으로 표시되고 인터넷이 되면 다시 조회해 바로잡는다. 영상 정보 조회(`process=False`)와 `/c/`·`/user/` 채널 주소 → 채널 id 조회(`_channel_info`)는 리눅스에서 실제 YouTube 로 확인했고, Windows PC 에서는 아직 확인하지 않았다.
- **위치**: `source.py` `from_channel_cache`·`_lookup`·`_channel_info`
- **임시방편**: 보관함에서 출처 배지를 눌러 직접 고르면 그것이 이긴다. 채널을 다시 불러오면(영상 id 가 생김) 제목으로 맞추지 않는다.

## I-028 | 2026-10-07 | 컷 리듬 맞추기: 나눈 컷이 모두 확대 컷으로 셈
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 스타일 가편집(#7)은 긴 말 컷을 배운 컷 길이로 나누고, 나눈 조각끼리 원래 크기 ↔ 확대를 번갈아 씀(카메라 한 대로 두 대처럼). 안 나눈 컷·시범 장면은 원래 화면 그대로이고, 확대 컷이 거의 없는 스타일은 나눈 곳만 1.08배로 살짝 당김(스타일 설명에도 적음). 그래도 레퍼런스보다 확대 컷이 많아질 수 있어 일치 점수의 '컷 리듬'은 오르고 '확대'는 내려갈 수 있음.
- **위치**: `editor.py` `_split_rhythm`·`_rhythm`, `style.py` `edit_params`
- **해결 방향**: 확대 대신 좌우 위치 바꾸기(리프레임)를 섞으면 확대 컷 수를 레퍼런스에 맞출 수 있음 (이번 범위 밖).

## I-027 | 2026-10-06 | 단어 단위 받아쓰기 실기 미검증 (절전 막기 · large-v3-turbo 단어 시각·힌트)
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 받아쓰는 동안 절전 막기(`SetThreadExecutionState`)는 Windows에서만 동작해 개발 PC에서는 가짜로만 시험했다. 단어 시각·`hotwords`·힌트 길이(토크나이저로 셈)는 tiny 모델과 가짜 모델로만 확인했고, 기본 모델 `large-v3-turbo`로 실제 한국어 강의를 받아써 보지 않았다.
- **위치**: `core._keep_awake`·`_whisper_opts`·`_analysis_session`, `captions.echo`
- **임시방편**: 힌트 목록을 그대로 따라 쓴 구간은 버린다(tiny 모델이 말소리 없는 곳에서 그렇게 받아씀). 절전 막기가 실패해도 받아쓰기는 그대로 한다.
- **해결 방향**: Windows PC에서 30분 넘는 촬영본으로 편집점 찾기 → 도중에 절전으로 안 들어가는지, 용어 인식·자막 나누기가 괜찮은지 본다.

## I-023 | 2026-10-06 | 누끼 모델을 크기·sha256 확인 없이 받음
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 누끼 모델(BiRefNet 약 220MB·u2net_human_seg 약 170MB)은 rembg 릴리스 주소에서 받아 크기·sha256을 확인하지 않고 그대로 쓴다. 얼굴·표정 모델은 둘 다 확인한다. 받다 깨지거나 바뀐 파일을 걸러 내지 못한다 (`SECURITY_GUIDELINES.md` 5번 규칙의 예외).
- **위치**: `thumb.py` `_model` → `fetch_model`(`size`·`sha256` 인자를 넘기지 않음), `BG_MODELS`·`MODEL_URL`
- **해결 방향**: 두 모델의 크기·sha256을 확인해 `BG_MODELS`에 고정값으로 넣고 `fetch_model`에 넘긴다.

## I-022 | 2026-10-06 | 최소 지원 Python 버전이 정해지지 않음
- **상태**: 열림 (기본값을 넣음 · 소유자 확인 필요) — 2026-10-07 `시작하기 (Windows).bat`·`setup_check.py` 가 3.10~3.14·64비트(x64)만 쓰고(3.13 우선), 맞지 않거나 깨진 `.venv` 는 옮겨 두고 새로 만든다 (D-028). 3.10 은 2026-10 지원 종료라 더 새 Python 이 있으면 bat 이 옮겨 간다. 소유자가 범위를 확정하면 `setup_check.PY_MIN`·`PY_MAX`·README 를 맞춘다.
- **심각도**: 중간
- **증상/내용**: (고치기 전) 사용자는 python.org에서 받은 아무 버전이나 쓴다(`py -3`). 코드는 3.10 이상 기능(`TemporaryDirectory(ignore_cleanup_errors=…)`)을 쓰지만 어디에서도 버전을 확인하지 않는다. 3.9 이하면 업데이트 설치(`updater.download_and_install`·`--selftest`)와 Deno 설치(`core._install_deno`)가 실패하고, 막 나온 Python이면 onnxruntime·ctranslate2 Windows 휠이 아직 없어 첫 설치(pip)가 실패할 수 있다.
- **위치**: `시작하기 (Windows).bat`, `README.md` 설치 안내, `updater.py`·`core.py`
- **해결 방향**: 지원 범위를 정해 `DEPENDENCY_POLICY.md` 3번에 적고, README 안내와 `.bat`의 버전 확인을 맞춘다.

## I-020 | 2026-10-06 | 편집실·스타일·검수·분석에 저장소 안 단위 테스트가 없음
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: `editor.py`(저장·자동 가편집·내보내기), `style.py`, `qa.py`, `core.analyze`·`render`는 `tests/`에 단위 테스트가 없다. 저장소 밖 e2e 묶음(I-018)이 유일한 안전망이라, 그 묶음을 돌릴 수 없는 환경에서는 회귀를 잡지 못한다.
- **위치**: `tests/`, `editor.py`, `style.py`, `qa.py`, `core.py`
- **해결 방향**: 이 코드를 고칠 때 특성 테스트부터 `tests/`에 더한다 (`REFACTORING_GUIDELINES.md` 3번, `TESTING_GUIDELINES.md` 5번).

## I-019 | 2026-10-06 | 남아 있는 린트 경고 (기준선)
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 2026-10-06 커밋 기준 `python3 -m pyflakes *.py`는 1건, `ruff check --no-cache`는 4건을 낸다. 새 작업은 이보다 늘리지 않는다 (`CODING_STANDARDS.md` 3번).
  - pyflakes·ruff F401: `thumb.py:8` 안 쓰는 `import subprocess`
  - ruff만: `qa.py:77` E741(`I`), `style.py:66` E731(lambda 대입), `style.py:69` E741(`O`)
- **위치**: `thumb.py`, `qa.py`, `style.py`
- **해결 방향**: 그 파일을 고치는 작업에서 함께 정리하거나 `# noqa`로 이유를 남긴다.

## I-018 | (썸네일 원본은 2026-10-07 저장소 thumb_src/ 로 옮김 · e2e 묶음은 아직 밖) | 2026-10-06 | 썸네일 편집기 원본과 e2e 시험이 저장소 밖에 있음
- **상태**: 열림
- **심각도**: 높음
- **증상/내용**: 썸네일 편집기 원본과 e2e 시험이 리드 세션의 scratchpad(`$SCRATCH`, `AGENTS.md` 5번)에만 있다. 그 폴더가 지워지면 썸네일 편집기 원본과 회귀 시험을 함께 잃는다.
  - 원본: `thumb_v2_head.html`, `tv2/p1_core.js`…`p7_auto.js`, `build_thumb.sh`(출력 위치가 `$SCRATCH/futsal-studio-repo/thumb.html`로 고정)
  - e2e: `th2_test.py`(썸네일), `ed2_test.py`(편집실, 193개 확인), `style_test.py`와 시험 앱 스크립트(`edtest2/srv.sh`·`sync.sh`, `ed2_clone.sh`, `th_run.sh`)·시험 영상
  - 작업 목록: `backlog.json`, `batch1_result.json`
- **위치**: `thumb.html`(빌드 결과, D-004), `$SCRATCH`
- **임시방편**: 빌드한 `thumb.html`은 커밋해 둔다. 원본을 찾을 수 없으면 작업 전에 소유자에게 알린다 (`ARCHITECTURE.md` 7절).
- **해결 방향**: 원본과 e2e를 저장소로 옮긴다.
  - 지금 `release.sh`는 `tests/`와 `manifest.json`만 배포에서 뺀다. 배포하지 않을 위치를 정하고 제외 규칙도 함께 고친다.
  - e2e에 박힌 경로(`edtest2/app` 동기화)와 포트도 함께 고친다.

## I-017 | 2026-10-06 | 그래픽카드 인코더 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 내보내기·촬영본 묶기는 짧게 시험 인코딩해 되는 인코더를 고른다(`h264_nvenc` → `h264_qsv` → `h264_amf`). 오류가 그래픽카드 문제(`HwEncError`)면 libx264로 다시 만든다. 실제 NVIDIA·Intel·AMD PC에서는 확인하지 못했다. 드라이버, 동시 세션 수, 화질 설정값(`-cq`·`-global_quality`·`-qp_*`)의 결과가 예상과 다를 수 있다.
- **위치**: `editor.py` `hw_encoder`·`_venc`·`HW_ERR`, `bundle.py` `_hw_encoder`·`_venc`
- **임시방편**: 실패하면 자동으로 CPU(libx264)로 다시 만든다. 느리지만 결과는 나온다.
- **해결 방향**: 그래픽카드 종류별 Windows PC에서 내보내기·묶기를 한 번씩 해 보고, 화질·속도·`studio.log`를 확인한다.

## I-016 | 2026-10-06 | release.sh가 작업 트리 전체를 커밋함
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: `release.sh`는 `git add -A`로 배포 커밋을 만들고 바로 main에 푸시한다. 다른 사람(AI)이 커밋하지 않은 작업이 있으면 그대로 사용자 PC에 배포된다.
- **위치**: `release.sh`
- **임시방편**: 커밋 안 한 작업이 있으면 깨끗한 클론에서 배포한다. 단위 테스트와 영향받은 e2e가 통과한 뒤에만 배포한다 (`DEVELOPMENT_RULES.md` 8번).
- **해결 방향**: 작업 트리가 깨끗하지 않으면 멈추게 한다 (소유자 결정 필요).

## I-015 | 2026-10-06 | 실행기 변경 뒤 작업 표시줄 고정 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 앱을 실행기 프로세스 안에서 켜고(D-013), 예전에 고정한 작업 표시줄 아이콘(`User Pinned\TaskBar\풋살사관학교 스튜디오.lnk`)을 PowerShell로 실행기 경로로 고친다. Windows에서 다음을 확인하지 못했다.
  - 켜진 창을 고정한 아이콘이 실행기를 거치는지
  - PowerShell 스크립트가 실제로 도는지
  - 시작 오류 알림 창(MessageBox)과, `python.exe`로 도는 `--selftest`
  - (2026-10-07) 바로가기는 이제 COM(`IShellLinkW`·`IPropertyStore`)으로 쓰고 AppUserModelID `FutsalAcademy.Studio` 를 바로가기와 앱 프로세스에 같이 준다 (`winlink.py`). venv 의 pythonw 는 진짜 Python 을 자식으로 띄우는 실행기라, 아이디가 없으면 창이 그 pythonw 로 묶여 고정 아이콘과 따로 보였다. 같은 실행 경로면 다시 만들지 않는다(`~/.futsal-studio/shortcut.json`). COM 이 안 되면 예전 PowerShell(아이디 없이). 모두 Windows 실기 미검증 — 예전에 고정한 아이콘은 탐색기를 다시 시작(로그아웃)해야 새 아이디를 읽을 수 있다.
- **위치**: `updater.py` `run_app`·`_start_failed`·`_alert`, `winlink.py` `prepare`·`write_shortcut`
- **해결 방향**: Windows에서 다음 순서로 확인한다: 켜진 창 고정 → 닫기 → 고정 아이콘으로 실행 → 업데이트 한 번. 작업 표시줄에 버튼이 하나만 보이는지도.

## I-014 | 2026-10-06 | Deno 자동 설치 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 처음 다운로드할 때 `deno.exe`를 받아 `~/.futsal-studio/bin`에 둔다(D-008). 받는 곳은 GitHub이고, 안 되면 dl.deno.land다. sha256과 `--version` 실행을 확인한 뒤에만 둔다. 하지만 Linux에서만 시험했다.
  - 백신이나 회사 방화벽이 막을 수 있다.
  - x86_64 zip만 받으므로 ARM Windows에서는 에뮬레이션으로 돈다.
- **위치**: `core.py` `ensure_deno`·`_install_deno`
- **임시방편**: 실패해도 멈추지 않고 하루 뒤 다시 시도한다. 일부 영상이 안 받아질 수 있다고 안내하고, '업데이트 확인'을 누르면 바로 다시 시도한다.

## I-013 | 2026-10-06 | 업데이트·저장 중 Windows 파일 잠금 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 백신·탐색기·OneDrive·켜진 프로그램이 파일을 잡고 있으면 `os.replace`가 실패한다. 실제 Windows에서 잠금 상황을 재현해 보지 못했다. 지금 대응은 다음과 같다.
  - 업데이트: 최대 12번 재시도하고, 실패하면 되돌린다.
  - 편집 프로젝트: 20번 재시도한다.
  - 올리기 키트: ` (2)` 이름으로 저장한다.
  - (2026-10-07) 저장·교체는 공통 도구 `updater.write_atomic`·`replace_retry` 로 모았다: 보통 8초(`REPLACE_SECS`), 막 만든 큰 영상(완성본·묶음·미리보기 파일)은 60초(`SETTLE_SECS`)까지 점점 길게 기다린다. 끝내 못 옮긴 완성본·묶음은 지우지 않고 '내보낸 영상_옮기지 못함_…'·'묶은 영상_옮기지 못함_…' 폴더로 남긴다 (I-036). 얼마나 오래 잡는지는 백신마다 다를 수 있다.
- **위치**: `updater.py` `_retry`·`_replace`·`replace_retry`·`write_atomic`, `editor.py` `_replace_retry`·`_place_final`, `upload.py` `_write_safe`, `bundle.py` `_replace_retry`·`_keep_tmp`
- **임시방편**: 업데이트는 실패하면 취소하고 이전 버전을 그대로 둔다. 그리고 "다른 프로그램이 앱 파일을 열고 있으면 닫고 다시" 하라고 안내한다.

## I-012 | 2026-10-06 | 2차 작업(배운 스타일대로 스스로 편집) 진행 중
- **상태**: 열림 (작업 중)
- **심각도**: 중간
- **증상/내용**: 아래 기능이 아직 끝나지 않았다. 코드 기준 현재 상태다.
  - NG 테이크·슬레이트·말더듬 정리: `takes.find_junk`를 `editor.recommend`에 연결하는 중이고, 아직 커밋 전이다.
  - 단어 단위 자막: 받아쓰기에 단어 시각·용어 사전, 새 프로젝트 자막 나누기, 노래방 효과의 단어 시각, 단어 추임새 컷까지 했다 (D-019, 커밋 전). 남은 것: 편집실 안 고치기 알림, 여러 영상 한꺼번에 받아쓰기. (이미 있는 프로젝트 자막 다시 나누기는 '자막 한 줄씩 나누기' 버튼으로 됨 · D-023)
  - 스타일 이벤트 기록·일치 점수(D-015): 아직 없다. `refs[]`에 영상별 요약만 있다.
  - 컷 리듬 맞추기(#7): 스타일 가편집이 긴 말 컷을 배운 컷 길이(도입·본론·마무리 3구간)로 단어 경계에서 나누고 번갈아 확대하며, 말이 촘촘한 곳만 최대 1.12배 빠르게 한다 (커밋 전 · I-028). 남은 것: 10구간 곡선, 구간 종류 나누기, 좌우 리프레임.
  - 썸네일 성능 개선: 진행 중이다.
- **위치**: `takes.py`, `editor.py` `recommend`·`auto_sequences`, `core.analyze`, `style.py`, `thumb.html`(원본 `tv2/`)
- **해결 방향**: NG 테이크 → 단어 단위 받아쓰기 → 이벤트 기록·점수 → 컷 리듬 순서로 진행한다 (뒤 작업이 앞 결과를 씀).

## I-011 | 2026-10-06 | pywebview 창 닫기·앞으로 가져오기 실기 미검증
- **상태**: 열림
- **심각도**: 중간
- **증상/내용**: 창을 닫으면 편집실의 `flushBeforeClose`로 저장을 끝낸 뒤(최대 약 6초) 닫는다. 앱을 또 켜면 `/api/focus`가 켜져 있는 창을 앞으로 가져온다(restore·show·on_top 토글). WebView2 + pywebview의 실제 Windows 동작은 확인하지 못했다: 닫기 이벤트 순서, 최소화된 창 복원, Windows의 포커스 뺏기 제한.
- **위치**: `app.py` `_on_closing`·`_focus_running`·`/api/focus`, `editor.html` `flushBeforeClose`
- **임시방편**: 편집실은 고친 뒤 0.6초 안에 자동 저장하고 5분마다 백업을 남긴다. 그래서 닫을 때 저장이 실패해도 잃는 양이 적다. 창이 앞으로 안 오면 작업 표시줄에서 직접 누른다.

## I-010 | 2026-10-06 | GitHub 태그 push가 403
- **상태**: 보류(업데이트에 영향 없음)
- **심각도**: 낮음
- **증상/내용**: `release.sh` 마지막의 `git push origin v<버전>`이 권한 문제로 403이 난다.
- **위치**: `release.sh`
- **임시방편**: 실패를 무시하고 안내 문구만 출력한다. 업데이트는 커밋에 고정된 zip을 쓰므로 태그와 관계없다 (D-011).

## I-009 | 2026-10-06 | WebView2 글꼴 렌더링 실기 미검증
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 편집실·썸네일·내보낸 자막은 앱에 넣은 글꼴(`fonts/`, `/fonts/`로 제공)을 쓰고, 스튜디오 화면(`ui.html`)은 jsDelivr의 Pretendard 웹폰트를 쓴다. Windows WebView2에서 다음을 확인하지 못했다.
  - 글꼴 로딩 시점·자간·굵기가 개발 브라우저(Chromium)와 같은지
  - 썸네일 캔버스의 글자 폭이 같은지
  - 화면 미리보기와 내보낸 영상 자막(ASS·libass, `FONT_K`)이 같은지
- **위치**: `fonts/`, `ui.html`, `thumb.html`, `editor.html`, `editor.py` `build_ass`
- **해결 방향**: Windows에서 같은 썸네일·편집본을 열어 개발 PC 스크린샷·내보낸 영상과 비교한다.

## I-008 | 2026-10-06 | 지원하지 않는 Mac 코드가 남아 있음
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: Windows 전용 결정(D-016) 뒤에도 Mac 코드가 남아 있고, 검증하지 않는다.
  - `시작하기 (Mac).command`, `ensure_shortcut`의 Mac 앱 만들기, `h264_videotoolbox`, `sys.platform == "darwin"` 분기
  - Mac 실행 파일은 `app.py`를 바로 켜서 업데이트 확인·되돌리기를 건너뛴다.
- **위치**: `시작하기 (Mac).command`, `app.py`, `editor.py`, `upload.py`
- **해결 방향**: 지울지 둘지는 소유자가 정한다. 지우면 README의 Mac 안내도 함께 정리한다 (I-007).

## I-007 | 2026-10-06 | README가 지금 앱과 다름
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: README '화면 구성'은 예전 4단계(… 러프컷: Claude가 준 컷 목록 붙여넣기 → 영상 + EDL)를 설명한다. 지금 화면은 7단계다(소재 찾기·보관함·편집점·편집실·썸네일·스타일 배우기·올리기). Mac 설치 안내도 남아 있다.
- **위치**: `README.md` (사용자 PC로도 배포됨)
- **해결 방향**: `ui.html`의 단계와 Windows 전용에 맞춰 고친다.

## I-006 | 2026-10-06 | 쓰지 않는 예전 러프컷 기능
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: `core.render`(컷 목록 → mp4 + EDL)와 `/api/render`는 지금 화면에서 부르지 않는다. 편집실 내보내기가 대신한다.
- **위치**: `core.py` `render`·`_tc`, `app.py` `/api/render`
- **해결 방향**: 소유자 확인 후 지운다 (`REFACTORING_GUIDELINES.md`).

## I-005 | 2026-10-06 | 쇼츠 코드 표기가 `shorts`와 `short`로 갈림
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 같은 개념인데 이름이 둘이다 (`DOMAIN_KNOWLEDGE.md` 1절).
  - 편집 프로젝트·올리기 키트·검수: `"shorts"`
  - 썸네일 편집기: `TPL.short`, `AUTO_FMT = "short"`
- **위치**: `thumb.html`(원본 `tv2/`), `editor.py`, `upload.py`, `qa.py`
- **임시방편**: 모듈 안에서는 일관된다. 모듈 사이로 값을 넘길 때만 주의한다. 새 코드는 `shorts`를 쓴다.
- **해결 방향**: 통일하려면 저장된 썸네일 문서·캐시에 이 값이 들어 있는지 먼저 확인한다.

## I-004 | 2026-10-06 | 얼굴 모델 없이 고른 장면은 모델이 생길 때까지 그대로
- **상태**: 열림 (의도된 동작)
- **심각도**: 낮음
- **증상/내용**: 모델 받기가 실패했을 때 고른 장면 후보는 예전 점수로 캐시된다. 캐시 지문에 `face.ready()`가 있어서, 모델 파일이 생기면 다시 고른다. 하지만 그 영상을 열 때마다 모델 받기를 다시 시도하지는 않는다.
- **위치**: `thumb.py` `_cand_sig`·`frame_candidates`, `face.py` `ensure`
- **임시방편**: 다른 영상에서 장면 고르기를 하면 모델을 받는다. 그 뒤에는 이 영상도 표정 점수로 다시 고른다. 오프라인 사용자가 열 때마다 기다리지 않게 하려는 선택이다.

## I-002 | 2026-10-06 | 스튜디오 작업은 중간에 멈출 수 없음
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 멈추기(✕) 버튼은 편집실 내보내기·검수·미리보기 파일 만들기에만 있다. 스튜디오 작업(보관함에 담기, 편집점 찾기, 촬영본 묶기, 스타일 배우기)과 썸네일 장면 고르기는 멈출 수 없다. 작업은 한 번에 하나라서 그동안 다른 작업도 못 한다.
- **위치**: `ui.html`, `app.py` `start_job`, `bundle.py`(복사 경로는 `core.run`)
- **임시방편**: 앱을 닫으면 끝난다 — 작업 중에 창을 닫으면 한 번 묻고(`app._confirm_close`), 닫으면 그 작업의 ffmpeg·pip·claude 도 같이 꺼진다 (Windows Job Object, I-040 · 예전에는 숨은 ffmpeg 가 몇 분 더 돌았음). 남은 임시 폴더(`out/.render_*`)는 다음 실행 때 정리된다.

## I-001 | 2026-10-06 | 여러 카메라가 섞인 촬영본은 순서가 틀릴 수 있음
- **상태**: 열림
- **심각도**: 낮음
- **증상/내용**: 촬영본 묶기는 파일 속 촬영 시각으로 순서를 정한다. GoPro 같은 카메라는 현지 시각을 UTC로 표시해 기록하므로, 휴대폰과 섞이면 순서가 어긋날 수 있다.
- **위치**: `bundle.py` `probe`·`sort_clips`
- **임시방편**: 정한 순서를 '작업 기록'에 남기므로 사용자가 확인할 수 있다. 원본은 그대로 남는다.

---

## 아카이브 (해결됨)

<!-- 분기별로 해결 항목 이동 -->

## I-043 | 2026-10-07 | Windows: 그 밖의 어긋남 (자막 BOM · NAS 경로 · 스타일 이름 · 바로가기 · 창 설정 · 인증서 · claude 찾기 · 오류 기록 · 끊긴 연결)
- **상태**: 해결(2026-10-07)
- **심각도**: 낮음~중간
- **증상/내용**: (1) 내보낸 `.srt`·`subtitles.srt` 가 BOM 없는 UTF-8 이라 프리미어·옛 메모장이 한글을 깨뜨림. (2) 작업 폴더가 NAS(`\\서버\공유`)면 프리미어 XML 주소에서 서버 이름이 빠져 모든 클립이 오프라인. (3) 스타일 이름 `CON`·`NUL`·`COM1`·붙여 넣은 탭은 배우기를 다 끝낸 뒤 저장에서 실패. (4) 켤 때마다 숨은 PowerShell(-EncodedCommand)로 바로가기를 다시 써서 지운 아이콘이 되살아나고 백신 행동 감시에 걸릴 수 있었음. (5) pywebview 5+ 는 기본이 비공개 모드라 화면 설정(보관함 필터·최근 색·타임라인 높이 등)이 켤 때마다 사라짐. (6) Python 3.13+ 의 엄격한 인증서 검사로 백신 HTTPS 검사·회사 프록시 PC 에서 업데이트·Deno·모델 받기가 '인터넷 확인' 오류. (7) Python 3.12.0 의 `which('claude')` 가 npm 의 확장자 없는 sh 스크립트를 돌려줘 '클로드 프로그램이 없어요'. (8) pythonw 는 `sys.stderr` 가 없어 traceback·스레드 오류·요청 오류가 모두 사라짐. (9) 미리보기를 앞뒤로 옮기면 WebView2 가 끊는 연결(ConnectionAbortedError 10053)이 요청 처리 밖으로 새고, 멈춘 미리보기가 연결·파일을 끝없이 잡음.
- **위치**: `editor.py` `_pathurl`·`export`, `core.py` `SRT_ENCODING`, `style.py` `clean_style_name`, `winlink.py`, `app.py` `_start_webview`·`_error_log`·`Handler`, `updater.py` `_ssl_context`, `claude_cli.py` `find_exe`
- **해결**: (1) `.srt` 는 `utf-8-sig` (읽을 때는 이미 BOM 을 지움). (2) UNC 는 `file://서버/공유/…`. (3) 금지·제어 문자를 빼고 60자, 예약 이름이면 ' 스타일' — 화면 `styleFileName` 과 같은 규칙. (4) `winlink.prepare`: 같은 실행 경로면 건너뜀(`shortcut.json`), COM 으로 쓰고 안 되면 PowerShell(60초 제한). (5) `webview.start(private_mode=False, storage_path=~/.futsal-studio/webview)`. (6) `VERIFY_X509_STRICT` 만 끈 공통 `updater.urlopen` (사슬·주소 확인은 그대로) + 인증서 오류면 백신 'HTTPS 검사' 안내. (7) PATHEXT 에 없는 확장자는 건너뛰고 `claude.exe`·`claude.cmd` 를 찾음. (8) `app._error_log` → `studio-error.log` (+ `faulthandler`), 켜다 멈춘 오류는 `updater._error_trace`. (9) `ConnectionError` 전체를 잡고 연결 제한 시간 120초, 오래된 거꾸로 재생 소리 정리 실패는 넘어감. 회귀 시험 `tests/test_windows_compat.py`.

## I-042 | 2026-10-07 | Windows: 업데이트의 구성요소 설치·실행기가 겹칠 때
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: (1) 앱이 불러 둔 .pyd/.dll 을 pip 가 못 바꿔(pip 23.3 미만) 업데이트가 '인터넷 연결 확인' 안내와 함께 되돌려짐. (2) pip 출력이 cp949 로 와서 UTF-8 로 읽어 한국어 오류·한글 경로가 '���'. (3) 아이콘을 거의 동시에 두 번 누르면 두 실행기가 같은 `*.rollback-tmp` 를 쓰거나 이미 지운 표시를 다시 읽어 '업데이트를 되돌리지 못했어요' 알림이 잘못 뜸. (4) 지난번 정리를 다 못 한 `.update_staging` 이 남아 있으면 `mkdir` 이 WinError 183 으로 멈춤. (5) Python 이 오래돼 pip 가 예전 yt-dlp 에서 멈춰도 '이미 최신이에요' (막히면 '크롬 로그인'이라는 틀린 안내).
- **위치**: `core.py` `update_app`·`_pip_self_upgrade`·`_pip_locked`·`_pip_advice`·`update_engine`·`_engine_needs_newer_python`, `updater.py` `check`·`_launch_lock`·`_deferred_pip`·`rollback`·`install`
- **해결**: D-027. (1) pip 를 23.3 이상으로 올린 뒤, 그래도 잠금이면 `.req_pending` → 다음 실행 때 실행기가 앱을 불러오기 전에 설치. (2) 파이썬 자식은 `updater.py_env()`(PYTHONIOENCODING·PYTHONUTF8). (3) `.launch_lock` 으로 한 번에 하나 + 잠금 뒤 표시를 다시 읽음, 임시 이름에 pid. (4) `exist_ok=True`. (5) Python 지원이 끝날 무렵이면 PyPI 의 최신 yt-dlp 가 받는 Python 을 확인해 '시작하기 (Windows).bat' 안내(작업 기록·화면 알림 · 막혔을 때도 먼저). 회귀 시험 `tests/test_windows_compat.py` Updates.

## I-041 | 2026-10-07 | Windows: 설치(bat)가 맞지 않는 Python·깨진 .venv·긴 경로·Visual C++ 없음에서 '설치 실패'만 반복
- **상태**: 해결(2026-10-07) — Windows 실기 미검증 (I-044)
- **심각도**: 중간
- **증상/내용**: `py -3` 이 휠 없는 3.15·ARM64·32비트를 골라 설치 실패. `.venv` 를 만든 Python 을 지우면 아이콘은 영어 오류 창, bat 은 파일이 있다고 venv 를 다시 만들지 않아 영원히 실패. 앱 폴더 경로가 길면(긴 경로 설정 꺼짐) onnxruntime 의 깊은 파일에서 pip 실패. Visual C++ 구성요소가 없으면 설치는 되는데 받아쓰기·누끼가 DLL 오류(소리·OCR 모델은 지웠다 다시 받기를 반복).
- **위치**: `시작하기 (Windows).bat`, `setup_check.py`, `core.py` `_whisper`·`dll_missing`, `thumb.py` `remove_bg`, `avmodels.py` `ensure`
- **해결**: D-028. 회귀 시험 `tests/test_windows_compat.py` Install (bat 은 CRLF·괄호 블록 안전 확인까지).

## I-040 | 2026-10-07 | Windows: 앱을 닫아도 남는 자식 프로세스 · 작업 중 창 닫기 · 절전으로 멈추는 긴 작업
- **상태**: 해결(2026-10-07) — Windows 실기 미검증 (I-044)
- **심각도**: 중간
- **증상/내용**: Windows 는 부모가 꺼져도 자식을 끄지 않아, 묶기(복사)·소리 꺼내기·pip·claude 가 창을 닫은 뒤에도 숨어서 돌며 파일을 잡음(다음 묶기의 정리·공간 확인이 틀어짐, pip 두 개가 같은 venv 를 고침). 작업 중에 창을 닫아도 묻지 않고 끝냄. 절전 막기는 편집점 찾기에만 있어 내보내기·받기·묶기·스타일 배우기는 자리를 비우면 PC 가 잠들어 멈춤.
- **위치**: `core.py` `popen`·`run`·`track`·`keep_awake`, `editor.py`·`style.py`·`plan.py`·`claude_cli.py` 의 자식 실행, `app.py` `start_job`·`_on_closing`·`_confirm_close`
- **해결**: 자식은 `core.popen`/`run` 으로만 띄워 `KILL_ON_JOB_CLOSE` Job Object 에 넣음(D-024). 작업 중이면 "지금 '…' 중이에요. 창을 닫으면 이 작업이 멈춰요" 확인. 모든 작업(`start_job`)이 `core.keep_awake()` 안에서 돔.

## I-039 | 2026-10-07 | Windows: 앱이 둘 뜨거나(SO_REUSEADDR) 8765 를 못 쓰면 아무 말 없이 빈 화면
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: 막 켜지는 중에 또 누르기·브라우저로 쓰는 중·PC 에 프록시 설정이 있으면 두 번째 실행이 같은 포트를 같이 잡아 앱이 둘 뜸(작업·저장·임시 폴더 정리가 섞임). Hyper-V·WSL·Docker 가 8765 를 예약했거나 다른 프로그램이 쓰면 10초 뒤 빈/남의 페이지만 열리고 studio.log 에도 남지 않음. 실행기도 남의 프로그램을 '켜져 있는 앱'으로 보고 업데이트 확인을 건너뜀.
- **위치**: `app.py` `_Server`·`_bind`·`_ours`·`_focus_running`·`/api/ping`·`/api/focus`, `updater.py` `_app_running`·`_app_ports`
- **해결**: D-025. 회귀 시험 `tests/test_windows_compat.py` Ports.

## I-038 | 2026-10-07 | 받다 끊긴 yt-dlp 중간 파일이 보관함 영상으로 보임 · 긴 작업 폴더에서 받은 파일 이름이 잘림
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: 절전·창 닫기·네트워크 끊김·백신 잠금으로 합치기 전에 끊기면 `….f137.mp4`(소리 없음)·`….temp.mp4` 가 남아 보관함에 하나 더 보였고, 편집점 찾기는 '소리를 꺼내지 못했어요'. 받는 중에도 잠깐씩 보였고, 출처 기록·학습용 옮기기가 진짜 영상 대신 그것을 고를 수 있었음. 목록과 `stat` 사이에 파일이 사라지면 `/api/state` 가 끊김. `trim_file_name` 이 폴더까지 포함해 120자로 잘라 긴 작업 폴더면 제목이 사라지거나 파일이 다른 폴더로 나감.
- **위치**: `core.py` `is_partial`·`is_video_file`·`local_videos`·`download`, `source.py` `_find_file`·`_library`, `refs.py` `_staged`·`_scan`
- **해결**: 받은 영상 이름 꼴(`YYYYMMDD_<id>_…`)의 `.f<형식>.`·`.temp.` 파일은 목록에서 뺌(사용자 파일 'game.f1.mp4' 는 그대로) · `stat` 실패는 건너뜀 · 폴더는 `paths.home` 으로 따로. 남은 중간 파일은 지우지 않음 (I-044).

## I-037 | 2026-10-07 | 확장자(또는 Windows 에서 대소문자)만 다른 영상이 분석·편집본·썸네일을 함께 씀
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: `IMG_1234.MOV`·`IMG_1234.mp4`(또는 OBS `.mkv`·바꾼 `.mp4`)가 같은 `analysis/IMG_1234`·`projects/IMG_1234.json` 을 써서 두 번째 영상이 첫 영상의 받아쓰기·편집본·미리보기 파일로 열리고, 다시 분석하면 첫 영상 결과를 덮어씀.
- **위치**: `core.py` `adir`·`library_dir`·`_alt_key`·`_folder_owner`, `refs.py` 옮기기·되돌리기·`projects_using`
- **해결**: D-026. 회귀 시험 `tests/test_windows_compat.py` LibraryNames.

## I-036 | 2026-10-07 | Windows: 막 만든 파일을 백신·탐색기·OneDrive 가 잡으면 결과를 잃음 (완성본·묶음·미리보기·썸네일 디자인·분석)
- **상태**: 해결(2026-10-07) — 잠금 시간은 Windows 실기 미검증 (I-013·I-044)
- **심각도**: 높음
- **증상/내용**: (1) 내보내기: 완성본 옮기기를 0.25초만 다시 해 보고, 원본이 잠긴 것인데도 '(2)…(99)' 이름으로 헛되이 다시 한 뒤 임시 폴더째 지워 렌더링 전체를 잃음(안내도 '같은 이름 파일이 열려 있어요'로 틀림). 묶기는 10초 뒤 같은 일. 미리보기 파일은 다시 시도가 없음. (2) 썸네일 디자인 저장: 지금 파일을 .bak 으로 먼저 옮긴 뒤 바꿔 끼우기가 실패하면 지금 파일이 없어지고, 요청은 응답 없이 끊겨 화면이 '저장 중…'에 멈춤(뒤로 가기는 반응 없음). (3) 편집점 찾기: 받아쓰기 뒤 `audio.wav` 를 결과 쓰기 전에 지우다 잠기면 받아쓰기를 통째로 잃음 · 결과 파일을 제자리에 써서 꺼지면 반쪽 transcript.json 이 '분석함'으로 보임. (4) 편집본 저장은 됐는데 백업·정리 실패로 '저장 실패'. (5) 가져오기·장면·정지 화면이 반쪽 파일로 남아 계속 쓰임. (6) 스타일 파일을 바로 덮어씀 (I-003).
- **위치**: `updater.py` `replace_retry`·`write_atomic`, `editor.py` `_place_final`·`make_proxy`·`save_upload`·`freeze_frame`·`save_project`, `bundle.py`, `thumb.py` `save_docs`·`grab`·`export_image`, `core.py` `_analyze`, `style.py` `learn`, `app.py` `/api/thumb/save`·`upload`·`export`·`/api/style/delete`, `thumb_src/parts/p2_view.js`·`p5_canvas.js`·`p6_ops.js`·`p7_auto.js`
- **해결**: 공통 도구로 (D-024): 큰 영상은 60초까지 기다리고 끝내 못 옮기면 보이는 폴더로 남겨 위치를 알림, 같은 이름 대상이 열려 있을 때만 다른 이름. 썸네일은 지금 파일을 끝까지 두고 .bak 은 복사, 실패하면 500 JSON → 화면이 '저장 실패 · 잠시 뒤 다시' 후 다시 저장, 뒤로 가기는 실패하면 물어봄. 그림 올리기·내보내기·브러시 저장도 실패하면 JSON 으로 답하고 화면이 알림(예전: 레이어 그림 주소가 비어 깨짐). 분석 결과는 임시 파일 → 바꿔 끼우기(타임라인을 맨 마지막), 소리 파일 지우기는 곁가지. 가져오기는 끝까지 받은 것만, 장면·정지 화면은 성공한 것만 제 이름으로. 회귀 시험 `tests/test_windows_compat.py` Locks·AnalyzeResults.

## I-035 | 2026-10-07 | config.json 을 메모장으로 고치면(BOM·ANSI·역슬래시 하나) 앱이 아예 안 켜짐 · 기록도 엉뚱한 폴더에
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: 설정 화면이 없어 작업 폴더를 옮기려면 config.json 을 고쳐야 하는데, `"D:\풋살작업"`(탐색기 주소 붙여 넣기)·UTF-8(BOM)·ANSI 저장이면 core import 에서 멈춰 앱이 켜지지 않고, 실행기는 같은 오류를 삼켜 기본 폴더에 studio.log 를 남김. `"D:\new"` 는 줄바꿈으로 읽혀 폴더를 못 만듦. 빠진 외장 드라이브도 같은 결과(기록조차 없음). dict.json 도 BOM·ANSI 면 기본 사전으로 돌아가 다음 저장 때 덮어씀.
- **위치**: `updater.py` `read_config`·`loads_tolerant`·`workspace`·`studio_log`, `core.py` `CONFIG`·`_open_workspace`·`CONFIG_NOTES`, `captions.py` `load_dict`, `app.py` `_after_start`
- **해결**: BOM·cp949·하나뿐인 역슬래시(끝 역슬래시 포함)를 읽고, 그래도 못 읽으면 기본 설정 + 안내(작업 기록·화면 알림). 쓸 수 없는 작업 폴더면 기본 작업 폴더로 열고 안내. dict.json 은 못 읽으면 그 내용을 `dict.json.bad` 로 남김. 회귀 시험 `tests/test_windows_compat.py` NotepadJson.

## I-034 | 2026-10-07 | 반쪽 이모지(JS .slice 가 자른 대리 문자) 하나로 화면이 멈추고 스타일 목록이 계속 깨짐
- **상태**: 해결(2026-10-07)
- **심각도**: 높음
- **증상/내용**: 제목에 이모지가 있는 영상으로 이름 없이 스타일 배우기 → `niceName(…).slice(0,12)` 가 🔥 를 반으로 잘라 `'…🔥\ud83d 스타일'` 이 서버로 옴. Windows 는 그 이름의 파일을 만들 수 있어 저장은 되지만, `log()` 의 studio.log 쓰기가 UnicodeEncodeError(ValueError 라 `except OSError` 를 지나침)로 작업이 실패하고, 그 줄이 든 `/api/state` 가 응답 없이 끊겨 화면(작업·진행률·보관함)이 다시 켤 때까지 멈춤. 다시 켜도 `/api/style/list` 가 매번 끊겨 모든 스타일이 사라져 보임(파일을 손으로 지울 때까지). 썸네일 자동 제목(24자)·편집본 이름으로 만든 제목(16자)도 같은 이유로 저장이 말없이 실패. v1.9.2 의 cp949 print 오류(I-030)와 같은 종류.
- **위치**: `ui.html`·`editor.html`·`thumb_src/parts/p1_core.js`·`p7_auto.js`(`cutText`), `app.py` `Handler._body`·`_send`·`log`, `core.py` `clean_text`·`clean_json`, `style.py` `clean_style_name`·`_repair_name`, `thumb.py` `export_image`, `updater.py` `studio_log`
- **해결**: 화면은 글자 단위로 자름(`cutText`). 서버는 모든 POST 본문을 `core.clean_json` 으로 고치고(짝 없는 것은 '�'), 응답은 UTF-8 로 못 쓰면 `\uXXXX` 로, `log()` 는 고친 글을 `errors="replace"` 로 쓰고 ValueError 도 넘어감. 스타일 이름은 반쪽·금지·제어 문자를 빼고, 예전에 반쪽 이름으로 저장된 파일은 목록을 볼 때 고친 이름으로 바꿈. 회귀 시험 `tests/test_windows_compat.py` HalfEmoji (node 로 세 화면의 자르기 도우미를 직접 돌림).

## I-003 | 2026-10-06 | 스타일 파일을 바로 덮어씀
- **상태**: 해결(2026-10-07)
- **심각도**: 낮음
- **증상/내용**: `style.learn`은 `styles/<이름>.json`을 `write_text`로 바로 쓴다. 저장하다 꺼지면 깨진 파일이 남는다. `list_styles`는 깨진 파일을 말없이 건너뛰므로 그 스타일이 목록에서 사라진다. 불변식 "임시 파일 → 바꿔 끼우기"(`DOMAIN_KNOWLEDGE.md` 4절)의 예외다.
- **위치**: `style.py` `learn`·`list_styles`
- **해결 방향**: 다른 저장처럼 임시 파일에 쓴 뒤 `os.replace`로 바꾼다.
- **해결**: `updater.write_atomic`(임시 파일 → 바꿔 끼우기, 잠금이면 다시)으로 저장 (I-036 묶음).

## I-021 | 2026-10-06 | `/api/thumb/cut`의 장면 주소 이름을 검사하지 않음
- **상태**: 해결(2026-10-07)
- **심각도**: 낮음
- **증상/내용**: 누끼 요청의 `src`가 `/frame?name=…&t=…`이면 그 `name`을 `editor.safe_name` 없이 `thumb.grab`에 넘긴다. `core.VIDEOS / name`·`core.adir(name)`이 보관함 밖을 가리킬 수 있다. POST는 같은 출처만 받으므로 다른 사이트는 이 경로를 쓸 수 없다.
- **위치**: `app.py` `do_POST` `/api/thumb/cut`
- **해결 방향**: `qq["name"][0]`도 `editor.safe_name`·`video_path`를 거치게 한다 (`SECURITY_GUIDELINES.md` 2번 순서).
- **해결**: 장면 주소 안의 이름도 `editor.video_path`(이름 검사 + 보관함 안의 파일)를 거친다. 회귀 시험 `tests/test_windows_compat.py` Names.


## I-026 | 2026-10-06 | 편집실 미리보기가 내보내기와 다름: 노래방 효과 시각 · 쇼츠 편집본 자막 나누기
- **상태**: 해결(2026-10-07)
- **심각도**: 중간
- **증상/내용**: (1) 자막에 단어 시각(`wt`)이 있으면 내보낸 영상의 노래방 효과는 단어를 말한 때에 채우는데, 편집실 미리보기(`editor.html` `effect()`)는 아직 글자 수에 비례해 채운다. (2) 가로 영상 자막은 롱폼형(두 줄·22글자)으로 저장되고, 쇼츠 편집본을 내보낼 때(영상·.srt·올리기 키트)만 `sh`(나눌 곳)대로 쇼츠형(한 줄 12글자·2초)으로 나눠 보인다. 미리보기(`capsTL()`)는 아직 한 덩어리로 보인다.
- **위치**: `editor.html` `effect()`·`capsTL()`, `editor._karaoke`·`_cap_words`·`_shorts_parts`
- **해결 방향**: 미리보기도 같은 규칙으로. `wt`는 1/100초 정수로 첫 값은 첫 단어 시작, 그 뒤는 앞 값과의 차이(누적합 → [시작0, 끝0, 시작1, 끝1, …]), 낱말은 `text.split()`과 같은 순서이며 수가 맞을 때만 쓴다. 노래방: 첫 단어는 자막 시작부터, 단어 k는 그 단어 시작부터 채움. 쇼츠 편집본: `sh`의 번호(몇 번째 낱말부터 새 자막)에서 나누고, 나눈 자막은 다음 자막의 첫 단어 시작까지 보인다.
- **해결**: 미리보기 `capWords`·`capParts`·`effect()`가 `editor._cap_words`·`_shorts_parts`·`_karaoke`와 같은 규칙으로 바뀌고 `SHORTS_SPLIT`을 켰다 (D-023). 노래방은 두 줄 자막·띄어쓰기 여러 번도 같은 규칙(검토 후 보강). node로 JS를 그대로 돌려 비교하는 `tests/test_caption_oneline.py` PreviewParityTest·KaraokeParityTest.

## I-030 | 2026-10-07 | Windows: 쪼개진 한글 자모가 든 파일 이름이면 스타일 배우기 등 작업이 'cp949' 오류로 멈춤
- **상태**: 해결(2026-10-07, v1.9.2)
- **심각도**: 높음
- **증상/내용**: 유튜브 제목이 잘리며 '정형ᄃ'처럼 자모가 남은 파일(예: `…_뭉쳐야 찬다 정형ᄃ.mp4`)을 다루면 `app.log()`의 print 가 cp949 콘솔에 못 써서 예외 → 작업 실패('배우지 못했어요 · 'cp949' codec can't encode…').
- **위치**: `app.py` `log()`
- **해결 방향**: 해결 — 시작할 때 stdout/stderr 를 UTF-8(errors=replace)로 바꾸고, print 실패는 무시(파일 기록은 UTF-8). 회귀 테스트 `tests/test_console_encoding.py`.

## I-025 | 2026-10-06 | 편집실 e2e 도구가 예전 서버를 상대로 검사할 수 있었음
- **상태**: 해결(2026-10-06)
- **심각도**: 중간
- **증상/내용**: `ed2_clone.sh`가 시험 폴더를 지우고 다시 만들 때, 그 폴더에서 돌던 예전 서버(cwd가 "(deleted)")가 포트를 계속 잡고 있었다. 새 서버는 포트를 못 잡고 꺼지고, e2e는 예전 코드를 검사했다.
- **위치**: `$SCRATCH/edtest2/srv.sh`, `$SCRATCH/ed2_clone.sh` (저장소 밖, I-018)
- **해결 방향**: 해결 — 같은 폴더("(deleted)" 포함)·같은 포트의 시험 서버를 끄고 필요하면 강제로 끔. 새 서버가 이 폴더에서 떴는지 확인하고, 아니면 실패로 끝냄.

## I-024 | 2026-10-06 | 1.8.0: 검정·흰색 전환, 위 트랙 전환이 들어간 영상의 내보내기 실패
- **상태**: 해결(2026-10-06, v1.8.1)
- **심각도**: 높음
- **증상/내용**: 1.8.0에서 Ctrl+D 기본 전환(가운데 검정)이나 V2 위 전환을 넣으면 내보내기가 'Conversion failed'로 끝났다. Python 3.10 이하 PC에서는 소리가 영상보다 늦게 끝나면 내보내기가 실패했다. '가편집 다시 만들기'가 자동 쇼츠의 복사본·이름 바꾼 편집본을 지웠다.
- **위치**: `editor.py` `_build_segment`·`export`, `editor.html` `recRebuild`·`seqDup`·`seqRen`
- **해결 방향**: 해결 — D-018. `concurrent.futures.TimeoutError`도 잡음. 복제·이름 바꾸기에 `auto: "user"` 표시. 회귀 테스트 `tests/test_export_trans.py`.
