# ARCHITECTURE.md — 아키텍처

> **목적**: 코드가 어떻게 조직되어 있고, 무엇이 무엇에 의존해도 되는지 정의한다.
> 구조 변경·새 모듈 추가·데이터 흐름 관련 작업 전에 반드시 읽는다.
> **갱신 시점**: 레이어/모듈/데이터 흐름이 바뀔 때. 코드와 문서가 다르면 문서를 고친다.

---

## 1. 아키텍처 개요

한 프로세스로 도는 로컬 데스크톱 앱이다. 모든 실행은 실행기 `updater.py --launch`를 거친다. 실행기는 업데이트 상태를 확인한 뒤 `runpy`로 `app.py`를 **같은 프로세스에서** 켠다. 이렇게 해야 작업 표시줄 고정이 유지된다.

`app.py`는 표준 라이브러리 HTTP 서버를 `127.0.0.1`에 띄우고 pywebview 창(WebView2)으로 화면을 연다. 화면은 단일 파일 HTML 3개이며, `fetch`로 JSON API를 부른다. 오래 걸리는 일(다운로드·분석·내보내기 등)은 **한 번에 하나**만 도는 백그라운드 작업으로 실행되고, 화면은 `/api/state`를 1초마다 폴링해 진행률과 로그를 받는다. DB는 없다. 모든 데이터는 작업 폴더(WORK)의 JSON·미디어 파일에 저장한다.

```
[바탕화면·시작 메뉴 아이콘 / 시작하기 (Windows).bat]
        │  pythonw updater.py --launch
        ▼
[updater.py] 업데이트 확인(import 검사·되돌리기) ──runpy──▶ app.py (같은 프로세스)
        ▼
[app.py]  ThreadingHTTPServer 127.0.0.1:FUTSAL_PORT(8765)  +  pywebview 창 (실패 시 브라우저)
   ├─ GET  /  /editor  /thumb      → ui.html · editor.html · thumb.html
   ├─ GET/POST /api/* · /media · /frame · /asset/* · /fonts/* · /stickers/*  → Handler (Host·Origin·파일 이름 검사)
   │     ├─ 바로 응답: 저장·목록·열기·스타일 적용 가편집 …
   │     └─ start_job(이름, fn) → 백그라운드 스레드 1개 ──core.set_progress──▶ /api/state (화면이 폴링)
   ▼
[기능 모듈]  core · editor · thumb · face · detect · thumbcopy · style · plan · refs · source · qa · bundle · upload · hooks · strategy · forecast · takes(2차 작업 중)
   ▼
[외부 도구]  ffmpeg(imageio-ffmpeg) · yt-dlp(+Deno) · faster-whisper · onnxruntime · Pillow/numpy · YouTube 공개 RSS(채널 전략)
   ▼
[작업 폴더 WORK]   videos/ analysis/ projects/ thumbnails/ styles/ refs/(학습용 영상) strategy/(채널 전략) edit_media/ out/ studio.log …
[~/.futsal-studio] bin/deno.exe · bin/cloudflared.exe · models/*.onnx · 엔진 기록(json) · remote.json(휴대폰 열쇠) · remote/(터널 설정·pid)
[앱 폴더]          config.json · .rollback/ · .update_* · .req_hash  (업데이트 상태)
```

**휴대폰으로 보기 (원격 접속, D-027 · 켜 둔 동안만)**

```
[휴대폰 브라우저 / 홈 화면 앱]  https://mulgyeol.kr/futsal  (정적 PWA · 와벨리 저장소 public/futsal/ · Vercel)
   │ ① PC 찾기: ntfy.sh/<비콘 주제> 마지막 글 → 기기 열쇠로 AES-GCM 풀기 → 지금 터널 주소
   │ ② API: https://<무작위>.trycloudflare.com/r/*  (서명 Authorization · CORS 는 mulgyeol.kr 만)
   │ ③ 영상·그림: …/r/m/<표>  (<video>·<img> 는 머리글을 못 보냄 → 기기·받은 곳(IP 대역)에 묶인 1시간 표)
   │ ⓪ 짝짓기: 코드 → PBKDF2 → 증명만 보냄 · 기기 열쇠·주제·주소는 코드 열쇠로 잠겨 옴 (Cloudflare 는 못 읽음)
   ▼
[Cloudflare 엣지 (TLS)] ⇄ 나가는 연결 ⇄ [cloudflared.exe 자식 프로세스 · core.popen(Job Object)] ── Host: remote.futsal.invalid ──┐
                                                                                                                  ▼
[app.py 프로세스]  로컬 서버 127.0.0.1:8765 (그대로 · 터널 뒤에 없음)      원격 리스너 127.0.0.1:<임의> (/r/* 만) = remote.RemoteHandler
                    └ /api/remote* (PC 창: 켜기·연결 QR·끊기·설정)            └ remote.Service → Bridge(log·start_job·작업 모습) → 기능 모듈
                                                                           └ Publisher 스레드 → ntfy.sh (암호 비콘 · 정해진 알림 문장)
```

## 2. 레이어와 책임

| 레이어 | 위치 | 책임 | 하면 안 되는 것 |
|---|---|---|---|
| 실행기 | `updater.py` | 실행, 업데이트 설치·검증, 되돌리기, 앱을 `runpy`로 실행, `--selftest` | 표준 라이브러리 밖 import. 앱 모듈 import(새 버전 확인은 별도 프로세스에서 `IMPORT_CHECK`로). 최신 Python 전용 문법 사용. 실행기가 망가지면 앱이 아예 안 켜지고 되돌릴 수도 없다 |
| 화면 (Presentation) | `ui.html` · `editor.html` · `thumb.html` | UI·입력, 편집 상태(편집실 프로젝트·썸네일 문서는 화면이 들고 있다가 저장 요청), 실행취소 스냅샷, 썸네일 렌더링·효과 캐시 | 로컬 파일에 직접 접근 (반드시 API 경유). 프레임워크·번들러 도입 |
| 원격 경계 | `remote.py` (`RemoteHandler`·`Service`) | 터널로 들어온 요청만: Host 표시·수 제한·CORS·서명/표 확인·허용 동작 목록, 짝짓기·기기 열쇠(`remote.json`)·비콘·알림·자동 끄기·절전 막기. 기능은 app 이 넘긴 `Bridge` 와 기능 모듈 함수를 부르기만 한다 | `app` import. 로컬 `Handler`의 API 를 터널에 내주기. 허용 목록 밖 동작(지우기·설정·업데이트·켜기·임의 경로). 비밀(코드·열쇠·주제·터널 주소·표) 기록 |
| HTTP 경계 | `app.py` (`Handler`) | 라우팅, Host·Origin 검사, 파일 이름 검사(`editor.safe_name`·`video_path`), 작업 시작(`start_job`), 예외를 JSON `{"error": …}`로 변환 | 무거운 처리 직접 구현. 기능 모듈 함수를 부르기만 한다. 지금 있는 얇은 조립(`_analyze`, 스타일 가편집 이름 붙이기)보다 늘리지 않는다 |
| 기능 모듈 (Service) | `editor` · `thumb` · `face` · `detect` · `thumbcopy` · `style` · `plan` · `avmodels` · `claude_cli` · `qa` · `bundle` · `upload` · `youtube_upload` · `youtube_api` · `hooks` · `takes` · `source` · `refs` · `strategy` · `forecast` · `worker` · `cutout_worker` · `exportplan` · `hwdec` · `idle` | 실제 처리, 작업 폴더에 파일 저장, 사용자에게 보일 한국어 오류 메시지 | HTTP 응답 조립. `app` import |
| 기반 | `core.py` | `config.json`, 경로 상수(WORK·VIDEOS·ANALYSIS·OUT), `ffmpeg()`·`run()`, 진행률, 다운로드 엔진, 받아쓰기·편집점, 업데이트 진입(`check_update`·`update_app`) | `updater`·`captions`·`intake`·`trouble`·`studiolog`(모두 표준 라이브러리만 쓰는 도우미, D-019·D-035~D-037)를 뺀 다른 앱 모듈 import |

## 3. 의존 방향 규칙

- 의존은 항상 **바깥 → 안** 한 방향: `app` → `upload`·`bundle`·`thumb`·`style`·`qa`·`editor`·`hooks` → `core` → `updater` → (표준 라이브러리만)
  - `source` → `core`, `hooks`(함수 안). `app`·`bundle`이 쓰고, `core`는 받은 영상 기록(`download`)·목록 채널 정보(`list_videos`)·`add_local` 때 **함수 안에서만** import한다 (순환이지만 import 시점이 달라 안전 · `core` 규칙의 예외는 이것과 `refs`·`captions`·`updater`뿐).
  - `refs` → `core`, `source`. `style`은 함수 안에서 지연 import한다(배우기·스타일 목록 · `style` → `plan` → `core` 순환을 피함). `app`이 쓰고, `core`는 이름 → 파일·분석 폴더 찾기(`video_file`·`adir`·`kept_sig`의 `_ref`)에서 **함수 안에서만** import한다 (D-022).
  - `captions` → (표준 라이브러리만 + `updater` 의 설정 JSON 읽기 `loads_tolerant`, 함수 안). `core`(받아쓰기)·`editor`(자막)·`app`(`/api/dict`)이 쓴다. `core`는 함수 안에서 import한다.
  - `intake`·`trouble`·`studiolog` → (표준 라이브러리만이 바탕 · 경로·오류·기록 위치를 인자로 받음). 예외는 함수 안 지연 import 뿐: `intake`는 ffmpeg 를 `core.run`/`core.popen`(Job Object)으로 띄우고 마지막 파일을 `updater.write_atomic`/`replace_retry`로 쓰며, `studiolog`는 비밀 지우기 규칙 `remote.redact`를 그대로 쓴다(D-044 · 규칙은 한 곳에만). `app`이 import 하고, `core`(편집점 찾기 직전 복사 중 확인·받기 실패 안내·원문 기록)는 함수 안에서, `editor`(내보내기 오류 위치)·`remote`(`_trace`)·`youtube_api`(로그인 결과 받기 오류 위치 · 업로드 주소 이상)는 `studiolog`만 import한다(D-049). `youtube_upload`·`remote` 는 `trouble`(유튜브 실패 카드)도 import한다. 기록 위치는 `app`이 `studiolog.setup(lambda: LOGFILE)`로 알려 준다.
  - `app`은 `updater`(업데이트 마무리·실행기 경유·공통 저장 도구)와 `winlink`(Windows 바로가기·작업 표시줄 아이디)를 직접 import한다. `face`는 `thumb`을 거쳐서만 쓴다.
  - `winlink` → (표준 라이브러리만: ctypes COM). `setup_check.py` 는 앱이 import 하지 않는 설치 확인 스크립트다 (`시작하기 (Windows).bat` 이 실행 · 오래된 Python 문법도 됨).
  - `editor`·`style`·`bundle`·`thumb`·`intake` → `updater` (공통 저장 도구 `write_atomic`·`replace_retry`, D-029). `claude_cli` 는 자식 묶음(`core.track`)만 함수 안에서 지연 import한다.
  - `upload` → `editor`, `hooks`, `core`. `thumb`(A/B 묶음 이름 `AB_SHEET`·`AB_TAGS` — 썸네일 확인에서 '모바일 비교'는 빼고 묶음에선 A 를 먼저)은 함수 안에서 지연 import한다(올리기 키트를 불러올 때 `thumb` 의 폴더 만들기가 돌지 않게 · D-067).
  - `youtube_upload` → `upload`(키트·`files_for`), `editor`(`safe_name`·`probe`), `core`, `updater`(`write_atomic`), `youtube_api`. `app`·`remote`(작업 이름 `JOB_NAME`·`JOB_FINISH` 를 `JOB_LABELS`·`STOPPABLE` 에 · D-048)가 쓴다.
  - `youtube_api` → `updater`(표준 라이브러리만 쓰는 실행기: 비밀 저장 `write_atomic(mode=0o600)` · HTTPS 인증서 설정 `_ssl_context` · D-048)뿐. 그 밖의 앱 모듈은 import 하지 않고, 불러올 때 아무것도 실행하지 않는다(DPAPI `ctypes.WinDLL`·Google opener 도 쓸 때만) — 실행기의 `import app` 확인이 안전하게. 새 두 파일은 `app.py` 의 import 줄과 같은 커밋으로 들어가야 한다(D-021 과 같은 주의).
  - `editor` → `core`, `takes`(2차 작업 중, 커밋 전), `captions`
  - `style` → `core`, `plan` · `qa`·`hooks` → `core`
  - `plan` → `core`. `style`·`avmodels`·`face`·`source`·`claude_cli`는 함수 안에서 지연 import한다 (`style`이 `plan`을 import하므로 순환을 피함).
  - `avmodels` → `core`, `thumb`(모델 받기 `fetch_model`) · `claude_cli` → (표준 라이브러리만). `app`이 `claude_cli`·`plan`을 직접 쓴다.
  - `takes` → (표준 라이브러리만). `editor`는 함수 안에서 지연 import한다.
  - `worker` → `core`(자식은 `core.popen` · 진행 표시 `set_progress`), `updater`(`py_env` · 파이썬 자식 UTF-8), `studiolog`(자식 오류 출력 끝). 자식은 `worker.py` 를 바로 실행해 `모듈:함수` 를 importlib 로 부른다(인자·결과는 표준 입출력 JSON). `editor`(`_mem_workers`)·`hwdec`(`_mem_ok`)는 남은 메모리 `avail_mb` 만 함수 안에서 지연 import한다 (D-051·D-057).
  - `cutout_worker` → `worker`, `core`(`_DLL_RE`·`vc_runtime_msg`), `studiolog`. `thumb`·`onnxruntime` 은 자식 안의 함수에서만 import한다. `app`(`/api/thumb/cut` 손 누끼)과 `thumb`(분석 자동 누끼 `_auto_cuts` · 함수 안 지연 import)이 쓴다 — 누끼 모델(`thumb.remove_bg`·`cut_auto`)은 자식 프로세스만 부른다 (D-051·D-068·D-069).
  - `exportplan` → `core`(`run`·`ffmpeg`) · `hwdec` → `core`(`popen`·`ffmpeg`). 둘 다 `editor`(내보내기)만 쓴다 (D-053·D-054).
  - `idle` → (표준 라이브러리만). 모델을 쥔 모듈(`face`·`detect`·`avmodels`·`thumb`·`core`)은 import 하지 않고 `sys.modules` 에 있을 때만 캐시를 비운다. `app` 이 `idle.start(LOCK, …)`·`JOB_HOOKS` 로, `editor` 가 작업 밖(스타일 가편집 · 바로 답하는 요청)의 얼굴 모델 쓰기를, `thumb` 가 검수 글자 읽기(`read_text` · `/api/thumb/ocr`)를 `idle.using()` 으로 감싸 쓴다 (D-055·D-068·D-069).
  - `strategy` → `core`, `forecast`, `hooks`(TERMS·조사 떼기·STOP), `refs`(추천 채널·학습용 기록의 채널 열쇠), `source`(채널 주소 → 열쇠), `updater`(RSS 받기 `urlopen`). `style`(배운 스타일 연결)·`claude_cli`(클로드 판단)는 함수 안에서 지연 import한다 (`style` → `plan` → `core` 순환을 피하고 앱 시작을 가볍게). `app`이 쓴다.
  - `forecast` → (표준 라이브러리 + numpy, numpy 는 함수 안에서). 파일·네트워크가 없는 계산만 한다. `strategy`만 쓴다.
  - `thumb` → `core`, `studiolog`(오류 위치), `updater`. `face`·`detect`·`avmodels`·`thumbcopy`·`trouble`·`editor`·`cutout_worker`·`worker`·`idle`는 함수 안에서 지연 import한다 (`cutout_worker` 의 자식이 `thumb` 를 불러오므로 순환을 피함).
  - `face`·`detect` → `core`, `thumb`(모델 받기 `fetch_model`) · `detect` 는 `studiolog`·`updater`(실패 표시 `write_atomic`)도
  - `thumbcopy` → `core`, `hooks`. `editor`·`claude_cli`·`updater`는 함수 안에서 지연 import한다. `app`이 `thumbcopy`를 직접 쓴다(문구·판정 작업). `remote` 는 작업 이름(`JOB_AI`·`JOB_JUDGE`·`thumb.JOB_ANALYZE`)만 쓴다.
  - `bundle` → `core`. `editor`는 함수 안에서 지연 import한다.
  - `remote` → `core`, `editor`, `qa`, `refs`, `source`, `strategy`(작업 이름만 · D-028), `style`, `thumb`·`thumbcopy`(썸네일 작업 이름만 · D-067), `tunnel`, `updater`(`write_atomic`·`urlopen`·`UA`·`NET_ERRORS`·`_why`). 리스너는 앱 화면 포트 창(`app_ports`)을 쓰지 않는다(D-034). 암호 부품(`Cryptodome`)은 함수 안에서만 (없어도 import 는 됨). `app`이 쓰고 `Bridge`(log·start_job·작업 모습·기록·`_analyze`·`_refs_job`)를 넘긴다.
  - `tunnel` → `core`, `updater` (받기·sha256·바꿔 끼우기·`write_atomic`·자기 확인 `urlopen`). `qr` → (표준 라이브러리만). `app`이 `qr`로 연결 QR 줄을 만든다.
  - 지연 import는 순환을 피하려는 기존 예외다. 새로 추가하면 이유를 주석으로 남긴다.
- 하위 레이어는 상위 레이어를 import 하지 않는다.
- 순환 의존이 생기면 구현을 멈추고 구조를 먼저 보고한다.
- 레이어를 건너뛰는 접근(예: UI에서 DB 직접 호출)은 금지. 예외가 필요하면 `DECISION_LOG.md`에 기록 후 진행.

## 4. 주요 모듈

| 모듈 | 위치 | 역할 |
|---|---|---|
| app | `app.py` | HTTP 서버·라우팅, `start_job`(작업 하나씩, 겹치면 409), `log()`, pywebview 창·닫기 전 저장, 이미 켜져 있으면 그 창을 앞으로(`/api/ping` 으로 이 앱인지 확인 → `/api/focus`), 포트 고르기(8765 → 8766~8799, `.port`), 작업 중 창 닫기 확인, pywebview 저장소(비공개 모드 끔), pythonw 오류 기록(`studio-error.log`), 재시작 |
| winlink | `winlink.py` | Windows 바탕화면·시작 메뉴 바로가기(COM `IShellLinkW`, 안 되면 PowerShell)와 작업 표시줄 아이디(AppUserModelID `FutsalAcademy.Studio`, 바로가기·프로세스 짝). 같은 실행 경로면 다시 만들지 않음(`~/.futsal-studio/shortcut.json`) |
| setup_check | `setup_check.py` | `시작하기 (Windows).bat` 의 설치 확인: 쓸 Python(3.10~3.14·x64 · 3.15 는 '너무 새로 나옴')·`.venv` 다시 만들기·긴 경로·Visual C++ 구성요소 (D-033) · 임시 폴더(압축 안에서 실행)·pip 실패 이유 한국어 한 줄 (D-058) |
| updater | `updater.py` | `--launch`(업데이트 확인 → `run_app`), `install`(zip 검사 → staging → 버전·sha256 → 문법·selftest → 백업 → 교체 → 지울 파일 정리), `rollback`, `check`/`finish`(새 버전 import 확인·알림), `.update_skip` |
| core | `core.py` | `list_videos`(조회수 순), `download`(받는 폴더·archive·진행 이름·출처 기록 함수를 바꿀 수 있음 · 학습용 영상이 씀), 이름 → 파일·분석 폴더(`video_file`·`adir`: 보관함 먼저, 없으면 학습용 영상 · `kept_sig`), `analyze`(whisper 한국어 → transcript.json·analysis.json·subtitles.srt·timeline.md), 엔진 관리(`update_engine`·`engine_autoupdate` 3일·`ensure_deno`), `check_update`·`update_app`(pip는 요구사항이 바뀔 때만). `render`(컷 목록 → mp4 + EDL)와 `/api/render`는 현재 화면에서 부르지 않는 예전 기능이다 |
| editor | `editor.py` | `probe`(ffmpeg 출력 파싱), 파형·썸네일 줄·미리보기(proxy), `recommend`(규칙 기반: 추임새·반복·무음 정리 tidy, 쇼츠 구간), `auto_sequences`(롱폼 가편집 + 쇼츠 1~3, 스타일 값 적용), 프로젝트 load/save(rev 충돌 검사·백업·복구·마이그레이션), `reanalyze_project`, `export`(ffmpeg 렌더·HW 인코더·Premiere XML·SRT·취소) |
| thumb | `thumb.py` | `frame_candidates`(v6: 선명도·밝기 × 흔들림 × 얼굴·표정(상한 2.2) × 액션(선수·공) × 머리 잘림 × 장면 표시 `scene_flags`(벤치·관중·뒷모습·끝에 걸림·작음·레슨 액션), 박힌 글자 상자 `text_boxes`·감점, 같은 화면 dHash 3장·클로즈업 6장까지, 후보 16개 · 장면마다 `kind`·`persons`·`ball`·`main`·`flags`·`band`·`tboxes`·자동 보정 `grade`(뽑힌 장면만), 캐시 `candidates6.json`), `analyze`(클로드 문구(동시에) → 장면 → 클로드 장면 고르기 `run_ai_frames`(동시에) → 주인공 자동 누끼 4장(서로 다른 장면 · `_auto_cuts` → `cutout_worker` 자식 하나 · 실패는 `cutFail` 한 줄) → 문구, `cached_analysis`), `auto_grade`, `grab`, `remove_bg`·`cut_auto`(누끼 ONNX → `clean_mask` 다듬기 → `cut_quality` 품질, 옆 JSON · `cutout_worker` 의 자식 프로세스만 부름), `read_text`(검수 OCR · `idle.using()`), 브랜드 키트 `load_brand`·`save_brand`(`thumbnails/brand.json`), `export_ab`(A/B 묶음 + 모바일 비교), 디자인 저장(`.bak`)·이미지 내보내기, `fetch_model`(크기·sha256 확인 후 제자리에 둠) |
| face | `face.py` | UltraFace 얼굴 + FER+ 표정 점수. `ensure()`가 처음에 모델을 받고, 실패하면 10분 동안 다시 시도하지 않고 조용히 False를 돌려줌 |
| detect | `detect.py` | YOLOX-nano(D-061)로 사람·공 찾기 `people(rgb)` → {persons[[x,y,w,h,확률]], ball}. `ensure`·`ready`(face 와 같은 실패 규칙), 레터박스·격자 해석·NMS |
| thumbcopy | `thumbcopy.py` | 썸네일 제목 문구: 주제어(`topics`)·규칙 틀(`rule_candidates` — 무조건 봐·비밀·못하는 진짜 이유·수비를 속이는 X·반전 O/X·대사 핵심 문장 …)·점수(주제가 작은 줄이면 감점)·다양하게 고르기(`suggest`), 사용자 클로드로 더 만들기(`run_ai` → `analysis/<stem>/thumb_copy.json`, 분석 때 자동 · `ai_state`: 로그인 확인됨이면 ready, 확인이 늦으면(unknown) maybe — 한 번 시도하되 20초만 기다림 · 실패는 1시간 기억), 검수 창 평가(`judge`) |
| style | `style.py` | `analyze_style`(컷·줌·자막 띠·무음·LUFS·말 빠르기), `merge`(여러 레퍼런스 평균), `edit_params`(→ `auto_sequences`의 style 인자, 기획 분석이 있으면 인트로 티저·강조 자막 값도), `learn`(영상마다 `plan.extract_plan`·`judge` → `plan`)·`list_styles`, `style_file`·`update_style`(다른 값은 그대로 두고 바꿔 끼우기) |
| plan | `plan.py` | 영상 기획 분석 (D-021). `extract_plan`(1초 한 장 640px 지문·복잡도·잔디·화면 글자 OCR(상한 400장)·소리 종류·화자 수 → `plan_events.json`), `detect`(티저·타이틀·정지·리플레이·삽입·흔들기·몽타주·웃음·효과음·펀치라인 줌·자막 사건 6종), `judge`(인트로·장르·형식·자막·재미 판단 문장 + 확신 + 근거 1~2개 · 채널 공식 한 문장 `headline`), `merge_plans`(길이×확신 투표 · 갈리면 `mixed` 표시), `plan_params`(절반 넘게 같은 판단일 때만 가편집 값), `claude_prompt`·`parse_ai`·`run_ai`(Claude 판단 저장) |
| avmodels | `avmodels.py` | 기획 분석 모델: PP-OCRv5 글자 찾기·한국어 읽기, YAMNet 소리 종류. `ensure`(처음에 받기 · ✕ 로 멈춤, 실패하면 10분 쉬고 조용히 False · 불러오지 못한 파일은 지워 다시 받게), `usable`(기획 기록 재사용 판단), `ocr`, `tags` |
| worker | `worker.py` | 무거운 작업을 따로 파이썬 프로세스에서: `call('모듈:함수', …)`(표준 입력·출력 JSON · 자식의 진행 표시를 부모로 · ✕/앱 끄기로 같이 꺼짐 · 죽으면 `WorkerError` · 자식의 작업 폴더는 부모가 연 `core.WORK` 그대로 — 환경 변수 `FUTSAL_WORKER_WORKSPACE` → `core._open_workspace`), `avail_mb()` 남은 메모리 (D-051 · D-069) |
| cutout_worker | `cutout_worker.py` | 누끼를 `worker` 로: 고품질인데 남은 메모리 < 7.5GB 거나 고품질 프로세스가 실패하면 빠른 누끼 + 안내. 자식은 `thumb.remove_bg` 그대로 (D-051) · 썸네일 분석 자동 누끼 `cut_auto`(자식 하나에서 장면 여러 장 · `thumb.cut_auto` 그대로 · 실패는 '자동 누끼를 따지 못했어요 · …') (D-069) |
| idle | `idle.py` | 마지막 작업 뒤 5분 동안 작업이 없으면 불러 둔 모델(얼굴·선수·공 찾기·글자·소리·앱 안 누끼·받아쓰기)을 내려놓고 메모리 반환 (D-055 · detect 는 D-069) |
| exportplan | `exportplan.py` | 내보내기: 같은 원본에서 이어지는 짧은 구간들을 ffmpeg 하나로 묶음(구간 그래프 그대로 + trim · split 앞 원본 형식 못박기 · 자막은 끝에 한 번 · 프레임 같음 · HDR·다시 보기는 안 묶음) (D-054) |
| hwdec | `hwdec.py` | Windows·HDR 영상에서 확인(화소·속도 · 20초 제한 · ✕ 로 끔)이 된 경우에만 `-hwaccel d3d11va` · 실패하면 일반 방식 (D-053, 실기 미검증 I-070) |
| claude_cli | `claude_cli.py` | 사용자 PC의 Claude Code CLI(사용자 클로드 계정)로 판단 받기: 실행 파일 찾기·`--help` 옵션 확인·`auth status`·보이는 창으로 설치/로그인·로그인 코드 저장·`run`(빈 임시 폴더, Read만, stdin, 제한 시간·멈추기, 한국어 오류) |
| qa | `qa.py` | `check_video`: 규격(16:9 / 9:16)·쇼츠 길이·검은 화면·멈춘 화면·소리 끊김·LUFS·피크를 점수로 |
| bundle | `bundle.py` | `make_bundle`: 촬영 시각 순서로 정렬해 같은 규격이면 그대로 이어 붙이고(copy concat), 다르면 다시 인코딩. 원본은 보존. 끝나면 app이 이어서 편집점 찾기 |
| upload | `upload.py` | `build_kit`·`save_edits`·`load_kit`: 제목 후보·설명(템플릿)·챕터·태그·썸네일 확인 → `out/<…>_올리기.txt/.json`. 유튜브 글자 수·챕터 규칙 적용. `files_for`: 올릴 영상·.srt 찾기(읽기만 · 유튜브 바로 올리기가 씀) |
| youtube_upload | `youtube_upload.py` | 유튜브에 바로 올리기 (D-046 · BR-023): 연결 상태 `status`(인터넷 안 씀 · 비밀 없음)·`save_client`·`start_login`·`logout`·`clear_all`, 설정 `settings.json`(화이트리스트), 미리 확인 `plan`(키트·파일·연결·채널·5000바이트·쇼츠·중복·감사·할당량), 작업 `run_upload`·`resume`(세션 파일로 이어 올리기)·`finish`(썸네일·자막·재생목록 마저 하기 · 올린 직후 5xx·404 는 다시), 처리 상태 `check`(작업 아님), 기록 `history.json`, 할당량 `quota.json`(태평양 시각 하루 · tzdata 없이), 정해진 주소 열기 `open_link`·`GUIDE_URLS` |
| youtube_api | `youtube_api.py` | Google OAuth 설치형 앱(루프백 `LoginFlow` · PKCE · 토큰 받기·새로 받기·취소), 비밀 저장(`save_secret`·DPAPI/권한 600 · 읽기는 이 PC 방식 머리만), 세션 주소 확인 `valid_session_uri`, 다른 주소로 보내는 응답 안 따라감(`_NoRedirect` · 인증서 설정은 `updater.urlopen` 과 같음 `_opener`), 클라이언트 JSON 읽기(`parse_client`), `Client`(재개 가능한 업로드 `start_upload`·`upload_status`·`upload_file` · `set_thumbnail` · `insert_caption` multipart · `playlists`·`create_playlist`·`add_to_playlist` · `video_status` · `channel_mine`), Google 오류 → 한국어(`classify`·`MSG`) |
| hooks | `hooks.py` | 우리 채널 목록을 `channel_cache.json`에 기억, 제목 틀·풋살 주제어(`TERMS`)·조사 처리로 제목 후보 생성 |
| takes | `takes.py` (2차 작업 중, 커밋 전) | `find_junk`·`is_slate`: 받아쓰기 구간만 보고 NG 테이크·슬레이트 말·말더듬 구간을 규칙으로 찾음 → `editor.recommend`가 가편집·쇼츠 후보에서 뺌 (`KNOWN_ISSUES.md` I-012). `find_fillers`: 단어 시각이 있으면 홀로 떨어진 추임새 단어 |
| source | `source.py` | 보관함 영상의 출처(풋살사관학교·다른 채널·내 촬영본·모름) 판단과 기록(`videos/sources.json`, D-020). 받을 때 yt-dlp 채널 정보 기록, 예전 영상은 채널 목록 기억 → 뒤에서 천천히 영상 정보 조회(`start_backfill`), 직접 고르기(`set_manual`), 다른 채널은 채널별 묶음·고정 색(`channels`), 고르기 칩 개수(`summary`) |
| refs | `refs.py` | 학습용 영상(스타일 배우기 전용) 보관함 (D-022): 기록 `refs/refs.json`(바꿔 끼우기·잠김 재시도·깨지면 `.bad`), 채널별 폴더·색(`source._register` 재사용, 보관함과 같은 색), `add_channel`(인기 영상 N개 → `core.download`를 받는 곳만 바꿔 → 채널 폴더 → '<채널명> 스타일' 배우기), `add_direction`(추천 방향 A/B/C), `prune`(배운 파일만 지우고 지문 `sig` 남김), `delete`(원본은 한 번 더 확인 · 빈 채널 폴더만 지움), `move_from_library`(보관함 → 학습용: 편집실 프로젝트에서 쓰는 영상은 건너뜀 `projects_using` · 분석 폴더 → 영상 → 기록, 실패하면 되돌림 · `archive.txt`에서 id 뺌), `restore`(학습용 → 보관함으로 되돌리기), `listing`(기록 없이 refs 하위 폴더에 있는 파일·받는 폴더에 남은 파일은 다시 기록 `_adopt`), `recommended`(`ref_channels.json`) |
| strategy | `strategy.py` | 채널 전략 (D-024): 기록 `WORK/strategy/`(state·channels·history·checkups·forecast, 바꿔 끼우기·.bad·잠기면 안 덮어씀), 경쟁 채널 넣기·빼기(열쇠·중복 막기), `refresh`(yt-dlp 목록 `core.channel_listing` + RSS `fetch_rss`·`parse_rss` · 예절 BR-016 · 막히면 6시간 쉼·RSS 계속 · 채널마다 저장 · 다시 시작한 날), `channel_stats`·`cadence`, 제목 패턴(`formula_stats`·`topic_stats`·`series_stats`·`fixed_hashtags`), `group_summary`·`strengths`·배운 스타일 연결, `takeaways`(규칙 R-* · 8분류 · 맞춤 점수), 할 일(`todo_*`·`todos_for`), 방향 초안 `PRESETS`·`validate_strategy`, `forecast_result`(입력 해시 캐시 · 자료 받은 시각 기준 입력 · 한 번에 하나)·`forecast_preview`(저장 없는 미리 보기)·`own_pace`(지금 속도), `solution`(30/60/90 · 먼저 할 것 · 계획을 바꾸면)·`solution_view`(캐시 `solution.json`), `checkup`·`evaluate`·`band_at`·`forward_check`·`remind`, `claude_prompt`·`parse_ai`·`run_ai`, 화면 묶음 `overview`·`week_view`(이번 주 할 것). 채널 원본은 새로 고침·점검만 읽고(`load_channel`), 분석은 줄인 자료(`_read_slim`)를 기억한다 (D-026). 비교 데이터 `strategy_seed.json`(함께 배포) |
| forecast | `forecast.py` | 가능성(%) (D-025): `calibrate`(반응 회귀·분류 줄이기·σ·Theil–Sen 전환·탄력성·bootstrap·DEFAULTS), `own_posterior`, `simulate`(주 단위 · 공통 난수 · 지수 누적 · 되먹임), 목표·궤적·KPI, 민감도 `variants`, 표시 `show`(BR-015), `assumptions`, 검증(`loo_coverage`·`video_reliability`·`backtest`·`brier`), `inputs_hash` |
| remote | `remote.py` | 휴대폰으로 보기 (D-027): `Store`(`~/.futsal-studio/remote.json` · 기기 최대 5·90일 · 같은 브라우저는 바꿔 끼움), `Pairing`(10분 한 번 코드 · 증명 확인 · PBKDF2 만남 주제·잠금·증명 열쇠), `Auth`(FSR2 서명: host 포함 · nonce), `Tickets`(미디어 표: 기기·IP 대역·1시간), `Limiter`, `Publisher`(ntfy 비콘·알림·하루 한도), `Service`(켜기·끄기는 시도 번호로 · 터널 추적 · 오류 뒤 다시 켜기 · 끊기·설정·작업 끝 알림 `job_hook`(실패는 `trouble.explain` 의 쉬운 한 줄 그대로 · 일부만 실패도 '확인이 필요해요' · D-044)·자동 끄기·절전 막기), `RemoteHandler`(`/r/*` 만 · 로그인 정보 브라우저 고르기(`/api/browsers`·`cookies`)는 PC 화면에서만 · D-044), 허용 동작 `ACTIONS` |
| tunnel | `tunnel.py` | cloudflared 고정 판 받기(`ensure`) · 빠른 터널 지킴이(`Tunnel`: 등록 줄 확인·http2 다시·다시 켜기 한 시간 6번·`core.popen`/`core.run` 으로 띄워 앱의 Job Object 하나에(따로 두지 않음 · D-034)·남은 pid 정리) · 출력 줄은 기록하지 않음 |
| qr | `qr.py` | QR 만들기(바이트·M·버전 1~10) → 줄 문자열 (PC 창이 SVG 로 그림) |
| intake | `intake.py` | 보관함에 들어오는 영상 (D-035 · D-042): `copying`(1초마다 본 크기·수정 시각이 5초 그대로 + Windows 쓰기 손잡이 없음), `busy`(편집점 찾기 직전), `unusable`(.MTS 등 못 쓰는 형식), `sig`·`remember`·`changed`(편집점을 찾을 때의 크기·수정 시각 `file_sig.json` · `write_atomic`), `clear_media_cache`(바뀐 파일로 다시 찾을 때 반쪽 파일 캐시 지움), `convert`(못 쓰는 형식 → MP4 · ffmpeg 경로는 부르는 쪽이 줌 · `core.run`/`core.popen` · 마지막 파일·원본 옮기기는 `replace_retry`), `annotate`(`/api/state` 목록에 copying·changed) |
| trouble | `trouble.py` | 작업 오류 → 쉬운 한 줄 + 할 일 (D-036): `explain`(규칙 `_RULES` 위에서부터 · 우리 한국어 안내는 그대로 + 환경 원인의 할 일), `Trouble`(이미 쉬운 말인 오류), `BROWSERS`·`browser`(로그인 정보를 읽을 브라우저 목록·검사), 유튜브 올리기 실패 카드 `YT_CARDS`·`youtube_card`·`youtube_kind_of`·`explain(…, youtube=True)`(정해진 문장만 · D-049) |
| studiolog | `studiolog.py` | 오류 기록 하나로 (D-037 · D-044): `write`(studio.log · 연도 붙은 시각 · 2MB 넘으면 studio.old.log · 비밀은 `remote.redact`), `where`·`trace`(오류 위치 한 줄 → studio.log · traceback 전체 → 오류 출력 = pythonw 면 `app._error_log` 의 studio-error.log · 같은 오류는 한 번만), `install_hooks`(스레드·메인의 잡히지 않은 오류 · 파이썬 기본 출력 대신), `session_start`·`job`·`session_end`(실행 표시 `studio.running.json` → 갑자기 꺼짐) |
| captions | `captions.py` (2차 작업 중, 커밋 전) | 용어 사전(`dict.json` 읽기·쓰기, 받아쓰기 힌트 `prompt`·`hotwords`, 힌트를 따라 쓴 구간 찾기 `echo`), 낱말 경계 고치기(`apply_dict`·`fix_words`), 자막 나누기(`chunk`·`from_segments`, BR-013) |
| 휴대폰 화면 (저장소 밖) | 와벨리 저장소 `public/futsal/` (`index.html`·`app.js`·`proto.js`·`app.css`·`sw.js`·`manifest.webmanifest`) | `https://mulgyeol.kr/futsal` 정적 PWA. `proto.js` 는 `remote.py` 와 짝(코드 정리·PBKDF2·AES-GCM·서명 — `tests.test_remote_proto`가 node 로 맞물림 확인). 와벨리 코드와 섞지 않음 |
| 화면 | `ui.html` · `editor.html` · `thumb.html` | 스튜디오 8단계(소재 찾기·보관함·편집점·편집실·썸네일·스타일 배우기·올리기·채널 전략 · 5·7단계에 '전략에서 만든 할 일' 상자), 편집실(`/api/edit/*`), 썸네일(`/api/thumb/*` · 원본 `thumb_src/parts/p1~p9` — p1 렌더러(자동 보정·전술 도형·맞춤 상자), p8 'AI 추천 썸네일'(레퍼런스형 템플릿 `T_NEW` 롱폼 11·쇼츠 8 + 예전 `TPL.long` 7·`TPL.short` 5(새 템플릿이 모자랄 때만), 배경 놓기 `frameLayer`(머리 지키기·박힌 글자 피하기)·`topLayouts`·`framesBelow`, 점수 `scoreDoc`(머리 잘림·가림 게이트, 장면 품질 `frameQ`), 고르기 `recommend`, 개발용 `window.__thumbAuto`), p9 전술 그래픽 메뉴·점 핸들·스티커·브랜드 키트·검수(모바일 미리보기·OCR·클로드 평가)·A/B 묶음) |

## 5. 데이터 모델 요약

모든 것의 키는 **보관함 영상의 파일 이름**(`videos/<이름>`)이다. 영상별 폴더와 파일 이름은 `core.adir(name).name`(확장자를 뺀 이름, 끝의 공백·점 제거)을 쓴다.

- 영상 1 : 1 분석 폴더 `analysis/<stem>/`
  - `transcript.json` [{start, end, text, words[{w, s, e, p}]}] (예전 받아쓰기는 `words` 없음), `analysis.json` {silences, loud_peaks}
  - `subtitles.srt`, `transcript_timeline.md`, `waveform_50.json`, `thumbs2.jpg/json`
  - `frames/`(장면 캡처, `candidates6.json`), `thumb_copy.json`(클로드 문구, 받아쓰기·제목 지문), `thumb_frames_ai.json`(클로드 장면 점수, 후보 장면 시각 지문), 묶음 영상이면 `bundle.json`, 편집점을 찾기 시작할 때의 영상 크기 `file_sig.json` {size} (예전 분석에는 없음 · D-035)
- 영상 1 : 1 편집 프로젝트 `projects/<stem>.json` = {source, info, captions[{id, start, end, text, words?}], sequences[], active, media[], v: 2, rev}
  - 프로젝트 1 : N 시퀀스(편집본) = {id, name, format: `long`|`shorts`, tracks[V1~3, A1~3], items[](V/A 클립: media·start·in·out·speed·link·fx 키프레임), trans[], markers[], titles[], shapes[], captionStyle, layout, master{volume, normalize, lufs}, duck, auto: `rough`|`style`}
  - 백업: `projects/backup/<stem>__YYYYMMDD_HHMMSS[_태그].json`. 자동 백업은 5분마다 최근 10개, 태그 백업(재분석전·덮어쓰기전·변환전)은 따로 10개
- 영상 1 : 1 썸네일 문서 `thumbnails/<stem>.json` {designs[]} (+ `.bak`). 이미지는 `thumbnails/assets/`(캡처·누끼·자동 누끼 `cut_auto_<키>.png` + 품질 `.json`·올린 그림). 브랜드 키트 하나 `thumbnails/brand.json` {logo, logoPos, colors{hl, hl2, accent, neon, box}, font, series, seriesOn, handle(예전 값 · 화면에서 안 씀), apply, aiCopy(분석 때 클로드 문구·장면 고르기)}
- 스타일 N : M 레퍼런스 영상: `styles/<이름>.json` = 합친 프로필(cutsPerMin·avgShot·medianShot·zoomCutsPerMin·avgZoom·pauseP75·captionRatio/Pos/Color·lufs·charsPerSec) + `refs[]`(영상별 프로필) + `plan`(영상 기획 분석: intro·genre·format·captions·fun·summary·apply·agree, Claude 판단 `ai`) · `refs[i].plan`(영상마다). `plan`이 없는 예전 파일도 그대로 읽는다
  - 영상마다 `analysis/<stem>/style_events.json`(구조, D-015)과 `plan_events.json`(기획 신호: 판 `v`·파일 `sig`·그때의 모델 상태·지문·OCR 줄·소리 점수·화자 수)을 따로 둔다
- 결과물 `out/`: `<stem>_<편집본>.mp4/.srt/_premiere.xml`(같은 이름이 있으면 ` (2)`…), 썸네일 `<stem>_<라벨>_<n>.jpg/png`·A/B 묶음 `<stem>_썸네일_A.jpg`…`_모바일 비교.jpg`(같은 이름이 있으면 ` (2)` · 비교 한 장은 올리기 키트 썸네일 확인에서 뺌), 올리기 키트 `<…>_올리기.txt/.json`, 렌더 임시 폴더 `.render_*`(켤 때 정리)
- 유튜브 바로 올리기 (D-046): 작업 폴더 `youtube/` = `settings.json` {v, madeForKids(null = 아직 안 고름), notify, thumbnail, captions, playlistId, playlistTitle, audited, consentMode, channelOk, preauditAck} (공개 설정은 기억하지 않음 · D-047) · `uploads/<sha1(이름\n편집본)[:16]>.json`(올리던 세션: key, name, seq, file, where(out·videos), size, mtime, quick(크기+앞뒤 1MiB sha1), mime, meta{title, description, tags, categoryId, privacy, publishAt, madeForKids, notify, shorts}, want{thumbnail{file}, captions{file, where}, playlist{id, title}}, channel{id, title}, uri(true = 시작함 · 주소는 사용자 폴더 `sessions/<열쇠>.bin` {v, sid, uri}), sid(짝 번호), createdAt, offset(Google 이 받았다고 한 바이트), state(starting·uploading·paused·failed), error, kind · 끝나면 둘 다 지움) · `history.json` {v, items[≤500]: {at, name, seq, file, title, videoId, privacy, publishAt, shorts, madeForKids, channel, url, studio, quick, size, want, steps{thumbnail·captions·playlist: {state(ok·skip·todo·needs_verify·quota·error), msg}}, locked, preAudit(감사 전에 올림 → 공개 불가), processing, problem(거절·처리 실패 안내), checkedAt, status{uploadStatus, privacyStatus, publishAt, rejectionReason, failureReason, processingStatus}}} · `quota.json` {v, day(태평양 시각 날짜), uploads, units, exhausted{uploads, units}}. 깨진 파일은 쓰기 전에 `.bad` 로 옮김(읽기·GET 은 아무것도 바꾸지 않음). 사용자 폴더 `~/.futsal-studio/youtube/client.bin`(클라이언트 ID·보안 비밀번호·프로젝트) · `token.bin`(refresh·access 토큰·만료·범위·연결 시각·채널 · 끊기면 열쇠를 빼고 relogin 표시) — 머리 `FSY1D`(DPAPI)/`FSY1P`(권한 600)
- 학습용 영상(스타일 배우기 전용, D-022) `refs/<채널 폴더>/<영상>` · 분석 기록 `refs/<채널 폴더>/_analysis/<stem>/`(style_events·plan_events·옮겨 온 받아쓰기) · 받는 중 `refs/_받는 중/`. 기록 `refs/refs.json` = {channels{채널 열쇠: {name, names, handles, url, color, folder}}, files{파일 이름: {channelKey, folder, videoId, title, how(download·move·found), kind, saved, pruned, sig, original·origin(확인하고 옮긴 원본 · 자동으로 지우지 않음)}}, ids{}, ui{bannerDismissed}}. 채널 열쇠·색 규칙은 `sources.json`과 같다. 같은 이름이 보관함에도 있으면 보관함이 먼저다(`core.video_file`·`adir`). 보관함에 파일이 없으면 옛 `analysis/<stem>`이 남아 있어도 학습용 기록이 먼저다. channels 의 `original`: '풋살사관학교'·'내 촬영본' 칸(원본)
- 보관함 출처 기록 `videos/sources.json` = {files{파일 이름: 기록}, ids{영상 id: 기록}, channels{채널 열쇠: {name, names, handles, url, color}}, own{ids, handles}(배운 우리 채널), lookup{영상 id: 조회 실패 시각·이유}}. 기록 = 채널 정보(channel·channelId·channelUrl·uploaderId·uploaderUrl)·kind·how(download·lookup·listing·cache·local·bundle)·manual·channelKey·manualChannel. 깨지면 `sources.json.bad`로 남기고 빈 기록으로 계속 (D-020)
- 채널 전략 `strategy/` (D-024): `state.json` = {v, own{name, channelId, handle, group, revivedAt, revivedManual}, competitors[{key, name, url, handle, channelId, group, from, addedAt}], removed[], strategy{direction, target[], targetNote, formats[{id, name, kind, perWeek}], days[], differentiation, series[{name, desc}], goals{m6, m12}, startedAt, updatedAt}, hidden[], todos[{id, text, category, use[], from{takeaway, channel, video}, createdAt, done, doneAt}], settings{remind, ownAuto}, pause{until, reason(blocked·fails), at}, ai{summary, diagnosis[], actions[], titles[], risks[], by, model, at, dataHash}} (예전 기록의 solution 칸은 읽을 때 버림) · `channels/<sha1(열쇠)[:16]>.json` = {key, name, handle, url, channelId, group, subs, subsAt, src, descFlags{lesson, contact, sponsor}(설명 원문은 저장 안 함), tabs{long|shorts{ids[], n, complete, sum, at, fullAt}}, videos{id: {k, t, te, o, d, v, vAt, pub, likes, seen}}, rss{at, ok, error, ids[]}, translated, errors[], at(마지막으로 무엇이든 받은 시각), listedAt(목록을 받은 시각 · RSS 만 받으면 그대로), seeded·origin(비교 데이터에서 출발했을 때)} — 분석은 형식마다 최근 120개 + RSS 영상만 남긴 줄인 자료를 기억하고, 새로 고침·점검만 원본을 읽음 · `history.jsonl`(한 줄 = {k, at, subs(그날 목록에서 받았을 때만 · 아니면 null), listed, n, sum, complete(목록을 받았을 때만), rss{id: 조회수}} · 깨진 줄은 건너뜀) · `checkups.json` {v, items[≤200] · 3일 안에 다시 점검하면 마지막 것을 바꿈} · 캐시 `forecast.json` {inputsHash, at, result} · 캐시 `solution.json` {hash, at}. 앱 폴더의 `strategy_seed.json`(비교 데이터 · 같은 채널 형식 · src 'seed')은 새로 고친 자료가 없을 때만 쓴다
- 그 밖에 `edit_media/`(편집실로 가져온 음악·이미지·영상·정지 화면), `analysis/_media/`(가져온 미디어 캐시), `channel_cache.json`, `upload_template.txt`, `dict.json`(용어 사전 {terms, fix, v}, 없으면 기본 사전), `studio.log`(2MB 넘으면 하나만 `studio.old.log` · 기록 줄 + 오류 위치 한 줄), `studio-error.log`(pythonw 일 때만: traceback 전체 + faulthandler · 1MB 넘으면 `studio-error.old.log`), 실행 표시 `studio.running.json` {pid, start, version, job, jobAt}(켜져 있는 동안만 · 정상 종료(창 닫기 — 작업 중이면 확인 뒤 · 업데이트 재시작)면 지움 · D-037)
- 휴대폰용 작은 미리보기 `analysis/_remote/<이름>_<sha1 10자>/preview.mp4` (최근 10개 · 완성본 폴더에는 안 넣음)
- 사용자별 `~/.futsal-studio/`: `remote.json`(휴대폰으로 보기: pc id·기기 열쇠·ntfy 주제·설정·seq, 권한 600), `remote/cloudflared.yml`·`tunnel.pid`, `bin/cloudflared.exe`, `bin/deno.exe`, `models/*.onnx`(+ OCR 글자 목록 `ocr-ppocrv5-korean-dict.txt`), `claude_token`(선택: 사용자가 붙여 넣은 클로드 로그인 코드), `youtube/client.bin`·`token.bin`·`sessions/*.bin`(유튜브 바로 올리기 · DPAPI), `engine_upgrade.json`·`deno_install.json`(엔진을 마지막으로 바꾼·실패한 시각)
- 앱 폴더의 업데이트 상태: `.update_staging/`, `.rollback/v<이전>/{files/, rollback.json}`, `.update_pending`, `.update_result`, `.req_hash`, `.update_skip` (모두 `.gitignore`에 있음)
- 규칙·불변식(예: 다시 분석해도 사용자 편집본을 덮어쓰지 않음, `config.json`을 보존함)은 `DOMAIN_KNOWLEDGE.md` 참고

## 6. 횡단 관심사 처리 방식

- **에러 처리**:
  - 기능 모듈은 **사용자에게 그대로 보여 줄 한국어 메시지**로 예외를 던진다. 예: `RuntimeError`, `updater.UpdateError`, `editor.Conflict`.
  - 작업 안에서 난 예외는 `trouble.explain`으로 쉬운 한 줄 + 할 일이 되어 `JOB.error`(한 줄)·`JOB.fail`(종류·할 일)·`log("문제가 생겼어요 · …")`로 화면(실패 카드)에 가고, 영어 원문과 오류 위치는 `studiolog`로 studio.log 에만 남는다 (D-036·D-037). 바로 응답하는 API는 `Handler`가 4xx/5xx + `{"error": …}`로 바꾼다(화면이 카드로 보일 것은 `fail`도 함께 · 예: 복사 중 409).
  - 여러 항목을 처리할 때는 하나가 실패해도 나머지를 계속한다(다운로드·스타일 배우기).
  - 선택 기능(얼굴 모델·Deno·엔진 자동 최신화)은 실패해도 멈추지 않고 대체 경로로 간다.
- **로깅**:
  - `app.log()`는 메모리 `LOG`(화면 '작업 기록', `/api/state?since=`), 작업 폴더 `studio.log`(`studiolog.write` · `YYYY-MM-DD HH:MM:SS` 접두어 · 2MB 넘으면 studio.old.log), stdout에 한꺼번에 남긴다.
  - 오류 위치는 `studiolog.trace(e)`로 studio.log 에만 한 줄(화면 기록에는 안 보임). 잡히지 않은 스레드·요청 처리 오류도 같은 한 줄 (D-037).
  - `updater.studio_log()`는 앱이 안 켜져도 같은 `studio.log`에 남긴다.
  - pythonw(사용자 PC)에서는 traceback·스레드 오류·서버 요청 오류·바깥 코드가 죽은 위치(faulthandler)가 작업 폴더의 `studio-error.log` 로 간다(`app._error_log`, 켜다 멈춘 오류는 `updater._error_trace`).
  - 진행률은 `core.set_progress(label, item, step, pct, detail)`로 알리고 `/api/state`의 `progress`로 전달된다.
  - 사용자 PC에서 문제가 생기면 `studio.log`를 받는다.
- **설정/환경변수 접근**:
  - `config.json`은 `core.CONFIG`(import 때 한 번 읽음)와 `updater.workspace()`(core 없이 같은 규칙)만 읽고, 둘 다 `updater.read_config`(BOM·ANSI·역슬래시 하나도 읽음, 못 읽으면 기본값 + `core.CONFIG_NOTES` 안내)를 거친다. 설정한 작업 폴더를 쓸 수 없으면 기본 작업 폴더로 연다(D-029).
  - 경로는 모듈 상수로 쓴다: `core.WORK/VIDEOS/ANALYSIS/OUT`, `editor.PROJECTS/ASSETS`, `thumb.THUMBS/ASSETS/MODELS`, `style.STYLES`.
  - 환경변수는 `FUTSAL_*` 몇 개뿐이다(`PROJECT_CONTEXT.md` 5절).
  - 업데이트는 `config.json`을 덮어쓰지도 지우지도 않는다(`updater.KEEP`). 파일이 없을 때만 넣는다.
- **인증/인가 체크 위치**: 로그인은 없다(로컬 1인용). 대신 `Handler`가 모든 요청에서 다음을 확인한다.
  - Host 헤더가 `127.0.0.1:PORT`/`localhost:PORT`인지 확인한다(DNS 리바인딩 차단).
  - POST는 Origin이 같은 출처인지 확인한다.
  - 영상·파일 이름 인자는 `editor.safe_name()`/`video_path()`로 경로·드라이브·`..`를 거절한다.
  - 파일을 내줄 때는 `resolve()` 후 허용 폴더 안인지 확인한다.
  - 서버는 `127.0.0.1`에만 bind한다. Windows 에서는 SO_REUSEADDR 를 끈다(`app._Server`·원격 리스너 `remote.RemoteServer` · 켜 두면 두 번째 실행이 같은 포트를 같이 잡음). 8765 를 못 쓰면(다른 프로그램·예약 포트) 8766~8799 중 하나로 켜고 작업 폴더 `.port` 에 남긴다. 두 번째 실행·실행기는 `GET /api/ping` 으로 그 포트가 이 앱인지 확인한 뒤에만 `/api/focus` 를 보낸다(D-030). 원격 리스너는 이 포트 창(`remote.app_ports`: 8765~8804 · `FUTSAL_PORT` 면 그 포트 하나)을 쓰지 않고, `/api/ping` 에는 403 이라 '이 앱'으로 잡히지 않는다(D-034).
- **동시성**:
  - 긴 작업은 `JOB` 하나다. 겹치면 409와 함께 "다른 작업이 끝난 뒤에 다시 눌러 주세요"를 돌려준다. 단 `/api/thumb/frames`·`/api/thumb/analyze`는 캐시가 있으면 바로 응답한다 (분석이 없으면 작업 '썸네일 분석', 클로드 문구·평가도 작업 하나 · 셋 다 `jobId` 응답 · 휴대폰에는 진행·알림 이름만, 시작은 PC 에서만).
  - 휴대폰에서 시킨 작업도 같은 `start_job`(`by`='휴대폰 · <기기>')이라 PC 작업과 겹치지 않는다. 작업이 끝나면 `app.JOB_HOOKS`(지금은 `remote.Service.job_hook`: 휴대폰 알림·검수 결과 기억)를 부르고, 훅이 실패해도 작업 결과는 그대로다.
  - `start_job` 은 작업 번호를 돌려주고, 작업 시작 응답에 `jobId` 가 있다. 끝난 작업의 결과는 `app.DONE`(최근 20개)에 남고 `/api/state?job=<번호>` 의 `done` 으로 받는다. PC 화면(`ui.html`·`editor.html`·`thumb.html`)은 '작업이 비었을 때의 결과'가 아니라 **자기가 시킨 번호의 결과**만 쓴다(휴대폰이 바로 다음 작업을 시켜도 안 섞임). `/api/state` 의 `job_id`·`job_by` 로 지금 작업이 휴대폰에서 시킨 것인지 안다.
  - 휴대폰이 시킨 작업은 `core.no_self_update()` 안에서 돈다(이 스레드에서는 다운로드 엔진 pip·Deno 설치를 안 함).
  - 원격 쪽 스레드(보내기 `remote-publisher`·5초 점검 `remote-watch`·Windows 절전 막기 `remote-awake`·`tunnel`)는 작업·HTTP 를 기다리게 하지 않는다(ntfy 가 느려도 큐에만 넣음).
  - 절전 막기는 `core.keep_awake()` 하나로만 쥔다(SetThreadExecutionState 를 부르는 곳은 `updater.awake_state` 하나 — `core._keep_awake` 가 그것이고, 실행기는 업데이트 마무리 동안 `updater._awake` 로 같은 것을 씀). Windows 는 그 상태를 스레드마다 세므로 작업 스레드(`start_job`)·같은 스레드 안의 편집점 찾기(`_analysis_session`, 들어올 때 상태로 되돌림)·`remote-awake`('켜 둔 동안 항상'·'작업할 때만')가 겹쳐도 한쪽이 놓을 때 다른 쪽 것이 풀리지 않는다(D-034).
  - 저장은 모듈별 잠금으로 보호한다: `editor._SAVE_LOCK`, `thumb._SAVE_LOCK`, `upload._LOCK`, `strategy._LOCK`(state·채널·기록 파일), `youtube_upload._LOCK`(설정·세션·기록·할당량·토큰 파일).
  - 유튜브 올리기·마무리는 `start_job` 작업이고, [멈추기](`/api/youtube/pause`)는 우리 작업일 때만 `editor.CANCEL` 을 켠다(내보내기의 ffmpeg 는 건드리지 않음). 업로드는 `CANCEL` 을 조각을 읽을 때마다 보고, 응답을 기다리는 중이면 연결을 끊는다.
  - 다운로드 엔진은 `core._ENGINE_LOCK`(pip 중에만)과 `_DENO_LOCK`으로 보호한다.
  - 멈추기(✕)는 `editor.CANCEL`, `run_killable`, `cancel_export`로 처리한다.
- **파일 저장 안전**:
  - 저장 순서: 임시 파일에 다 쓴다 → `os.replace`로 바꾼다. Windows 잠금에 대비해 재시도한다 — 새 코드는 공통 도구 `updater.write_atomic`·`replace_retry`(보통 8초, 막 만든 큰 영상은 `SETTLE_SECS` 60초)를 쓴다(D-029). 끝내 못 옮긴 완성본·묶음은 지우지 않고 보이는 폴더로 남긴다.
  - 편집 프로젝트는 `rev` 판 번호로 다른 창의 덮어쓰기를 막고(409 conflict), 백업한다.
  - 깨진 파일은 지우지 않고 옆에 `.bad`로 남긴 뒤 최근 백업으로 복구한다.
  - 썸네일 문서는 `.bak`을 남긴다.
- **외부 프로세스·미디어 정보**:
  - 외부 프로세스는 `core.run()`·`core.popen()`으로 실행한다(Windows `CREATE_NO_WINDOW` + 앱이 꺼지면 같이 꺼지는 Job Object `core.track` · 파이썬 자식은 UTF-8 출력 `updater.py_env`). 휴대폰으로 보기의 cloudflared(`--version` 확인·터널)도 같다 — 터널만의 Job Object 는 두지 않는다(D-034). 모든 작업(`start_job`)은 `core.keep_awake()` 안에서 돌아 PC 가 절전으로 들어가지 않는다.
  - ffprobe가 없으므로 미디어 정보는 `ffmpeg -i`의 stderr를 파싱해 얻는다(`editor.probe`, `bundle.probe`, `style._probe_duration`).
  - ffmpeg는 `core.ffmpeg()` 하나로만 찾는다.
- **업데이트 흐름**: 받기 → `testzip` → `.update_staging`에 풀기 → 버전·sha256 확인 → 모든 `.py` 문법 확인과 새 `updater.py --selftest` → 바뀔 파일을 `.rollback/v<이전>`에 복사 → `os.replace`로 교체(재시도) → manifest에서 빠진 파일 삭제(`config.json`·`.venv`·작업 폴더는 제외) → `.update_pending` 기록 → (core) 요구사항이 바뀌었으면 pip(`.req_hash` · Windows 는 먼저 pip 23.3 이상으로) → 재시작. 켜진 앱이 쓰는 .pyd/.dll 때문에 pip 가 실패하면 되돌리지 않고 `.req_pending` 을 남긴다(D-032). 다음 실행 때 실행기는 다음과 같이 처리한다.
  - 실행기끼리는 `.launch_lock` 으로 한 번에 하나다(겹친 실행은 앞 실행기가 끝낸 상태를 다시 읽음).
  - `.req_pending` 이 있으면 앱을 불러오기 전에 pip 를 하고, 실패하면 되돌린다.
  - 업데이트 다시 시작은 도는 작업이 없을 때만 정한다(`/api/restart` 가 작업 확인과 `app.RESTARTING` 표시를 한 잠금 안에서 · 휴대폰이 그사이 시킨 작업이 있으면 409 busy → 화면이 끝날 때까지 다시 부름). 정한 뒤로는 `start_job` 이 PC·휴대폰의 새 작업을 받지 않는다(실행기를 못 띄우면 표시를 지우고 그대로 켜 둠).
  - 업데이트 다시 시작(`app.restart`)의 순서: 휴대폰으로 보기 끄기(터널·리스너·마지막 비콘 · 켜 둠 표시는 그대로) → 실행기 띄우기(`FUTSAL_RESTART`·이 프로세스 번호 `FUTSAL_OLD_PID`) → 끝내기. 실행기는 `.launch_lock` 안에서 `.req_pending` 설치 전에 그 번호의 프로세스가 끝나길 기다린다(최대 `OLD_APP_WAIT` 20초 · .pyd 를 놓은 뒤에 pip) → 새 앱이 포트를 잡은 뒤 원격을 이어서 켠다(D-034). 이전 앱이 쥐던 절전 막기는 그 프로세스와 함께 풀리므로, 실행기가 확인·기다리기·pip·새 버전 확인 동안 `updater._awake` 로 이어 쥔다.
  - `import app, core, editor, thumb, style, qa`로 확인하고, 실패하면 되돌린 뒤 `.update_skip`에 기록한다.
  - 켜다가 멈추면 그 자리에서 되돌린다.
  - 창이 안 뜬 채로 1분이 지나서 두 번 더 켜면 되돌린다.
  - v1.7.1 이하 PC는 예전 단순 방식(zip을 받아 덮어쓰고 pip)으로 한 번 올라온다.
- **플랫폼**: Windows 기준이다(`sys.platform == "win32"` 분기). 하드웨어 인코더 후보는 nvenc → qsv → amf이고, 실패하면 libx264로 간다.

## 7. AI 작업 시 구조 관련 규칙

1. 새 파일은 기존 레이어 구조에 맞는 위치에 만든다. 새 최상위 디렉터리가 필요하면 먼저 제안한다.
2. 구조를 크게 바꾸는 리팩토링은 `REFACTORING_GUIDELINES.md` 절차를 따르고 사전에 계획을 보고한다.
3. 이 문서에 없는 패턴을 새로 도입할 때는 도입 이유를 `DECISION_LOG.md`에 남긴다.
4. **`updater.py`는 모든 실행이 거치는 문이다.** 표준 라이브러리만 쓰고, 앱 모듈을 import하지 않으며, 최신 Python 전용 문법(예: 3.12의 따옴표 겹친 f-string)을 쓰지 않는다. 고치면 `tests.test_update`와 `python3 updater.py --selftest`로 반드시 확인한다.
5. 저장소에 추적되는 파일은 `tests/`와 `manifest.json`을 빼고 **모두 사용자 PC로 배포된다** (`release.sh`). 저장소의 `config.json`은 새로 설치하는 PC의 기본값이 되므로 개발용 값으로 바꾸지 않는다.
6. **`thumb.html`은 빌드 산출물이다.** 원본(`thumb_src/head.html` + `thumb_src/parts/p*.js`, 저장소 안 · 배포 목록에서 제외)을 고치고 `python3 thumb_src/build.py`로 다시 만든다(명령은 `AGENTS.md` 5번). 저장소의 `thumb.html`만 고치면 다음 빌드 때 사라진다. 원본을 찾을 수 없으면 작업 전에 소유자에게 알린다. 소유자가 허락해 `thumb.html`을 직접 고쳤다면 "원본 동기화 필요"를 보고하고 `KNOWN_ISSUES.md`에 남긴다.
7. 새 API는 `Handler`에 추가한다. Host·Origin 검사 뒤에 두고, 파일 이름 인자는 `editor.safe_name()`으로 검사하며, 긴 처리는 `start_job` + `core.set_progress`로 한다. 앱 프로세스 밖의 서버·포트를 새로 열지 않는다.
   - 예외 하나 (D-027): 원격 접속을 켠 동안만 뜨는 루프백 원격 리스너(`remote.RemoteServer`)와 cloudflared 자식 프로세스. 휴대폰용 경로는 `Handler`가 아니라 `remote.RemoteHandler`에 두고 `SECURITY_GUIDELINES.md` 2절의 원격 경계 순서를 따른다. 로컬 `Handler`의 경로를 터널 쪽에 이어 붙이지 않는다.
8. 사용자가 만든 데이터(편집본·썸네일 디자인·설정)는 덮어쓰지 않는다. 다시 만드는 결과는 옆에 추가하고, 바꿀 때는 먼저 백업한다. 상세 규칙은 `DOMAIN_KNOWLEDGE.md`에 있다.
