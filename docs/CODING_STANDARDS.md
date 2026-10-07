# CODING_STANDARDS.md — 코딩 표준

> **목적**: 코드를 작성·수정할 때의 스타일과 품질 기준.
> AI가 생성하는 코드가 사람이 짠 기존 코드와 구분되지 않게 하는 것이 목표다.
> **우선순위**: 기존 코드 컨벤션 > 이 문서 > 언어 커뮤니티 관례. 충돌 시 상위를 따르고 보고한다.

---

## 1. 공통 원칙

- **읽는 사람(=미래의 소유자) 기준으로 작성한다.** 영리한 코드보다 뻔한 코드.
- **YAGNI**: 지시받지 않은 확장 포인트·추상화·설정 옵션을 미리 만들지 않는다.
- **일관성 우선**: 같은 문제는 프로젝트 안에서 같은 방식으로 푼다. 기존 유사 코드를 먼저 찾아 패턴을 따른다.
- 함수는 한 가지 일만. 대략 40줄을 넘으면 분리를 검토한다 (기계적 강제는 아님). 이미 긴 함수(`editor.export`·`_mix_audio_impl`·`_build_segment` 등)를 지시 없이 쪼개지는 않는다 (`REFACTORING_GUIDELINES.md`).
- 매직 넘버·매직 문자열 금지 — 의미 있는 상수로 추출한다. 기존 관례: 모듈 위쪽 대문자 상수 + 옆에 이유 주석 (`ENGINE_RETRY = 86400  # 켤 때 저절로 하다 실패했으면 하루 뒤에 다시 …`).
- **사용자는 Windows 를 쓰는 비개발자 한국인이다.** 화면·로그·오류 문구는 모두 친근한 해요체 한국어로, 전문 용어 대신 쉬운 말로 쓴다 ("편집점 찾기", "가편집", "누끼", "다른 작업이 끝난 뒤에 다시 눌러 주세요").
- **표준 라이브러리·단일 파일 우선**: 서버는 `http.server`, 화면은 HTML 한 파일. 웹 프레임워크·번들러·npm 을 들이지 않는다 (`DECISION_LOG.md`).
- **분석은 PC 안에서 규칙으로**: 편집점·가편집·스타일·올리기 키트는 AI 사용료 없이 계산한다. 유료 API 연동은 소유자 결정 사항이다.

## 2. 네이밍

| 대상 | 규칙 | 예 |
|---|---|---|
| 변수·함수 | Python snake_case / JS camelCase | `media_info`, `reanalyze_project` / `kfAt`, `trackState` |
| 모듈 안에서만 쓰는 것 | `_` 접두 | `_replace_retry`, `_SAVE_LOCK` |
| 클래스·예외 | PascalCase | `Handler`, `UpdateError`, `Conflict` |
| 상수 | UPPER_SNAKE_CASE | `TITLE_MAX`, `ENGINE_EVERY` |
| 파일 | Python 모듈·화면은 소문자 한 단어 (`editor.py` + `editor.html`), 테스트는 `tests/test_<영역>.py` | `upload.py`, `tests/test_upload.py` |
| 불리언 | `is_`/`has_` 접두 또는 기존 관례(`ok`, `…On`) | `has_audio`, `bgOn`, `captionsOn` |
| 저장·API JSON 키 | camelCase (Python 코드에서도 그대로) | `strokeW`, `fadeIn`, `cutsPerMin`, `keepPause` |
| API 경로 | `/api/<영역>/<동작>` | `/api/edit/save`, `/api/upload/kit` |
| 환경 변수 | `FUTSAL_` 접두 | `FUTSAL_PORT`, `FUTSAL_FFMPEG` |
| 사용자에게 보이는 파일 | 한국어 이름 | `<영상>_올리기.txt`, `묶음_YYYYMMDD_<제목>.mp4`, `studio.log` |

- 함수 밖으로 나가는 이름은 축약하지 않는다. 기존 코드처럼 짧은 범위 안의 관례적 한 글자(`n` 영상 이름, `p` 경로, `b` 요청 본문, `q` 쿼리/시퀀스, `it` 클립)는 허용.
- 한국어 도메인 용어의 영문 표기는 `DOMAIN_KNOWLEDGE.md` 용어집을 따른다 (같은 개념 = 같은 이름). 코드에 이미 있는 표기: 롱폼/쇼츠 `format: "long" | "shorts"`, 편집본 `sequences`, 자막 `captions`, 펀치인 `zoom`(`zoomEvery`·`_punch`), 스타일 프로필 `profile`, 올리기 키트 `kit`.

## 3. 포매팅·린트

- 포매터/린터 설정 파일은 **없다**. 기존 코드 모양이 기준이다: 4칸 들여쓰기, 큰따옴표, 긴 줄 허용(대략 200자 안), import 는 표준 라이브러리 → (빈 줄) → 앱 모듈(`import core` 처럼 모듈 단위). `black` 등으로 파일 전체를 재포맷하지 않는다.
- 린트 기준: 고친 `.py` 가 `pyflakes`(또는 `ruff check --no-cache`) 새 경고를 만들지 않는다 (명령은 `AGENTS.md` 5번, 지금 남은 경고는 `KNOWN_ISSUES.md` I-019). 일부러 남긴 것은 `# noqa: <코드>` 로 표시한다.
- HTML 안의 JS 를 고쳤으면 문법을 확인한다 (예: `node -e` 로 `<script>` 내용을 `new Function` 에 넣어 보기 — `build_thumb.sh` 가 하는 방식).
- 포맷 논쟁은 하지 않는다 — 기존 모양을 따른다.
- 수정하지 않은 코드까지 재포맷해서 diff를 오염시키지 않는다.

## 4. 주석

- **"왜"를 설명하는 주석만 작성한다.** 코드를 한국어로 번역하는 주석 금지.
- 우회책·제약이 있는 코드에는 이유와 `KNOWN_ISSUES.md` 참조를 남긴다. 기존 관례: Windows 사정을 한 줄로 (`# Windows: 백신·OneDrive 가 잠깐 잡고 있으면 조금 뒤 다시`).
- 모든 모듈 맨 위에 한국어 docstring 으로 그 파일이 하는 일을 적는다 (테스트 파일은 실행 방법까지). 공개 함수·복잡한 함수에도 짧은 한국어 docstring.
- 짝으로 있는 계산(`editor.html` ↔ `editor.py`)에는 `(editor.py 와 같은 규칙)` 처럼 상대편을 적는다.
- 주석 처리된 죽은 코드는 남기지 않는다 (git이 기억한다).
- `TODO(이슈참조):` 형식만 허용. 막연한 `TODO: 나중에`는 금지 — `KNOWN_ISSUES.md`로 보낸다. (지금 코드에는 TODO 주석이 없다)

## 5. 에러 처리

- 에러를 조용히 삼키지 않는다. 예외: 로그 파일 쓰기·창 앞으로 가져오기·임시 파일 정리처럼 **실패해도 되는 곁가지 작업**만 `except OSError: pass` 식으로 넘기고, 가능하면 좁은 예외 타입을 쓴다.
- 사용자에게 보이는 에러 메시지와 내부 로그를 구분한다. **이 앱에서는 예외 메시지가 곧 화면 문구다** (`app.start_job` 이 `trouble.explain(e)` 를 거쳐 화면 실패 카드에 띄움 — 우리 한국어 안내('…요'로 끝나는 앞 토막)는 그대로, 영어 원문은 쉬운 한 줄로 바꾸고 원문은 studio.log 에만) → `raise RuntimeError("…하지 못했어요 · <원인>. <다음에 할 일>")` 처럼 사용자가 읽을 문장으로 쓴다. 할 일 버튼까지 정하려면 `trouble.Trouble(kind, msg, actions)`. 자주 나는 새 영어 오류는 `trouble._RULES` 에 한 줄 더한다. 스택은 화면에 내보내지 않는다.
- 예상 가능한 실패와 버그를 구분한다:
  - 잘못된 입력 → `ValueError`/`FileNotFoundError`/`LookupError` → HTTP 400/404 + `{"ok": false, "error": "…"}`
  - 다른 작업 중 → 409 `"다른 작업이 끝난 뒤에 다시 눌러 주세요"`, 저장 충돌 → `editor.Conflict`(409)
  - 업데이트 실패 → `updater.UpdateError` (앱 폴더는 그대로이거나 되돌려 둔 상태여야 함)
  - 예상 못 한 오류 → `studiolog.trace(e)`(studio.log 에 위치 한 줄 + 콘솔이 있으면 traceback) + `log(...)` 후 500. `traceback.print_exc()` 만 쓰지 않는다 — pythonw 에서는 아무 데도 안 남는다 (D-029)
- **부가 기능은 실패해도 앱을 멈추지 않는다**: 얼굴·표정 모델, Deno 설치, 엔진 자동 최신화, 하드웨어 인코더는 실패하면 기록을 남기고 예전 방식으로 계속한다 (`face` → `None`, `HwEncError` → 소프트웨어 인코더).
- 한 항목 실패가 전체를 막지 않게: 여러 영상을 처리할 때는 실패한 것만 모아 알리고 나머지를 계속한다 (`style.learn` 패턴).
- 에러 처리 패턴: 예외 + 경계에서 처리 (HTTP 핸들러·`start_job`). Result 타입은 쓰지 않는다.

## 6. 로깅

- 로깅 도구: `logging` 모듈은 쓰지 않는다. `app.log(msg)` 하나로 화면 기록(`/api/state`) + 표준 출력 + 작업 폴더의 `studio.log`(`studiolog.write` · 시각 `%Y-%m-%d %H:%M:%S` · 2MB 넘으면 studio.old.log)에 남긴다. 화면에는 안 보이고 studio.log 에만 남길 것(영어 원문·오류 위치)은 `studiolog.write`·`trace`. 앱이 켜지기 전·실행기에서는 `updater.studio_log(app_dir, msg)`.
- 모듈은 `app` 을 import 하지 않고 `log` 함수를 인자로 받는다 (기본값 `print`). 진행률은 로그가 아니라 `core.set_progress(label=, item=, step=, pct=, detail=)`.
- 레벨 구분은 없다 — 모든 줄이 사용자 화면에 보이므로 사용자가 읽을 문장으로 쓴다. 단계 안의 세부 줄은 두 칸 들여쓴다 (`"  완료 · …"`), 실패는 `"…하지 못했어요 · {e}"`.
- `studio.log` 는 문제가 생기면 사용자가 관리자에게 보내는 파일이다 → 원인을 알 수 있게 파일 이름·원인을 함께 남긴다. 사용자 PC 에서는 `pythonw` 로 실행돼 `print`·`traceback` 출력이 보이지 않는다.
- **개인정보·시크릿·토큰(브라우저 쿠키 등)은 절대 로그에 남기지 않는다** (`SECURITY_GUIDELINES.md`).
- 커밋 전 임시 디버그 출력(console.log, print)은 제거한다.

## 7. 언어별 세부 규칙

### Python
- 사용자 PC 의 Python 버전은 고정돼 있지 않다 (python.org 에서 받은 것, 최소 지원 버전은 미정 — `DEPENDENCY_POLICY.md` 3번). 새 버전 전용 문법은 피한다 — 모든 `.py` 가 설치 전에 사용자 Python 으로 문법 확인을 받는다.
- 타입 힌트는 쓰지 않는다 (기존 관례).
- 파일 읽기·쓰기는 항상 `encoding="utf-8"` (Windows 기본값 cp949 로 깨짐), JSON 은 `ensure_ascii=False`. 경로는 `pathlib.Path`, 한글·띄어쓰기 경로를 전제로 한다. Windows 는 폴더 이름 끝의 공백·점을 지우므로 `core.adir` 처럼 미리 정리한다.
- 저장은 임시 파일에 다 쓴 뒤 `os.replace` 로 바꿔 끼우고, Windows 잠금(`PermissionError`)이면 잠깐 뒤 다시 시도한다. 편집본처럼 잃으면 안 되는 것은 `fsync` + 백업까지 (`editor.save_project`).
- 요청으로 받은 파일 이름은 `editor.safe_name` 을 거치고, 내보내는 파일은 `resolve()` 후 허용 폴더 안인지 확인한다 (`SECURITY_GUIDELINES.md`).
- 외부 프로그램은 `core.run(cmd)`(인자 목록, utf-8, Windows 검은 창 안 띄움 `core.NO_WINDOW`)으로, 멈추기(✕)가 필요하면 `editor.run_killable`. ffmpeg 는 `core.ffmpeg()`(imageio-ffmpeg) — ffprobe 는 없으므로 `ffmpeg -i` 출력을 읽는다.
- 무거운·선택적 서드파티(numpy, PIL, faster_whisper, onnxruntime, webview, yt_dlp)는 **함수 안에서** import 한다 (앱 시작·업데이트 import 확인을 가볍게, 없으면 예전 방식으로).
- `updater.py` 는 표준 라이브러리만 쓰고 다른 앱 모듈을 import 하지 않는다 (모든 실행이 거치는 문).
- 오래 걸리는 일은 `app.start_job(이름, fn)` 으로 한 번에 하나만 돌린다. 여러 스레드가 만지는 상태는 모듈 잠금(`_SAVE_LOCK`, `_LOCK`)으로 보호한다.
- 대상 OS 는 Windows 하나다. 기존 macOS·Linux 분기는 지우지 않되(삭제는 승인 필요), 새 기능에 Mac 대응을 따로 만들 필요는 없다. Linux 분기는 개발·테스트용이다.

### HTML / JS (`ui.html`, `editor.html`, `thumb.html`)
- 화면 하나 = HTML 한 파일 (CSS·JS 인라인). 빌드 도구·프레임워크·외부 스크립트를 넣지 않는다 (예외: `ui.html` 의 Pretendard 글꼴 CSS). 글꼴은 `/fonts/` 에서 받는다.
- `thumb.html` 은 직접 고치지 않고 소스 조각을 고쳐 다시 빌드한다 (`ARCHITECTURE.md` 7절 6번).
- 서버 호출은 같은 출처 `fetch('/api/…')` JSON (`api()`·`post()` 도우미). 서버가 Host·Origin 을 확인하므로 다른 주소로 부르지 않는다.
- 영상 제목·파일 이름처럼 밖에서 온 글자를 `innerHTML` 에 넣을 때는 `esc()` 로 감싼다.
- 색은 `:root` CSS 변수로. 단축키는 프리미어(편집실)·포토샵(썸네일)과 같게 맞춘다.
- 편집실 계산(키프레임·전환 범위·트랙 상태·화면 배치·색보정)은 `editor.py` 와 짝이다 — 함께 고친다.
