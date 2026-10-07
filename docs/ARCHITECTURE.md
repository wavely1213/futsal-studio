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
[기능 모듈]  core · editor · thumb · face · detect · thumbcopy · style · plan · refs · source · qa · bundle · upload · hooks · takes(2차 작업 중)
   ▼
[외부 도구]  ffmpeg(imageio-ffmpeg) · yt-dlp(+Deno) · faster-whisper · onnxruntime · Pillow/numpy
   ▼
[작업 폴더 WORK]   videos/ analysis/ projects/ thumbnails/ styles/ refs/(학습용 영상) edit_media/ out/ studio.log …
[~/.futsal-studio] bin/deno.exe · models/*.onnx · 엔진 기록(json)
[앱 폴더]          config.json · .rollback/ · .update_* · .req_hash  (업데이트 상태)
```

## 2. 레이어와 책임

| 레이어 | 위치 | 책임 | 하면 안 되는 것 |
|---|---|---|---|
| 실행기 | `updater.py` | 실행, 업데이트 설치·검증, 되돌리기, 앱을 `runpy`로 실행, `--selftest` | 표준 라이브러리 밖 import. 앱 모듈 import(새 버전 확인은 별도 프로세스에서 `IMPORT_CHECK`로). 최신 Python 전용 문법 사용. 실행기가 망가지면 앱이 아예 안 켜지고 되돌릴 수도 없다 |
| 화면 (Presentation) | `ui.html` · `editor.html` · `thumb.html` | UI·입력, 편집 상태(편집실 프로젝트·썸네일 문서는 화면이 들고 있다가 저장 요청), 실행취소 스냅샷, 썸네일 렌더링·효과 캐시 | 로컬 파일에 직접 접근 (반드시 API 경유). 프레임워크·번들러 도입 |
| HTTP 경계 | `app.py` (`Handler`) | 라우팅, Host·Origin 검사, 파일 이름 검사(`editor.safe_name`·`video_path`), 작업 시작(`start_job`), 예외를 JSON `{"error": …}`로 변환 | 무거운 처리 직접 구현. 기능 모듈 함수를 부르기만 한다. 지금 있는 얇은 조립(`_analyze`, 스타일 가편집 이름 붙이기)보다 늘리지 않는다 |
| 기능 모듈 (Service) | `editor` · `thumb` · `face` · `detect` · `thumbcopy` · `style` · `plan` · `avmodels` · `claude_cli` · `qa` · `bundle` · `upload` · `hooks` · `takes` · `source` · `refs` | 실제 처리, 작업 폴더에 파일 저장, 사용자에게 보일 한국어 오류 메시지 | HTTP 응답 조립. `app` import |
| 기반 | `core.py` | `config.json`, 경로 상수(WORK·VIDEOS·ANALYSIS·OUT), `ffmpeg()`·`run()`, 진행률, 다운로드 엔진, 받아쓰기·편집점, 업데이트 진입(`check_update`·`update_app`) | `updater`·`captions`(둘 다 표준 라이브러리만 쓰는 도우미, D-019)를 뺀 다른 앱 모듈 import |

## 3. 의존 방향 규칙

- 의존은 항상 **바깥 → 안** 한 방향: `app` → `upload`·`bundle`·`thumb`·`style`·`qa`·`editor`·`hooks` → `core` → `updater` → (표준 라이브러리만)
  - `source` → `core`, `hooks`(함수 안). `app`·`bundle`이 쓰고, `core`는 받은 영상 기록(`download`)·목록 채널 정보(`list_videos`)·`add_local` 때 **함수 안에서만** import한다 (순환이지만 import 시점이 달라 안전 · `core` 규칙의 예외는 이것과 `refs`·`captions`·`updater`뿐).
  - `refs` → `core`, `source`. `style`은 함수 안에서 지연 import한다(배우기·스타일 목록 · `style` → `plan` → `core` 순환을 피함). `app`이 쓰고, `core`는 이름 → 파일·분석 폴더 찾기(`video_file`·`adir`·`kept_sig`의 `_ref`)에서 **함수 안에서만** import한다 (D-022).
  - `captions` → (표준 라이브러리만). `core`(받아쓰기)·`editor`(자막)·`app`(`/api/dict`)이 쓴다. `core`는 함수 안에서 import한다.
  - `app`은 `updater`를 함수 안에서 직접 import한다(업데이트 마무리·실행기 경유). `face`는 `thumb`을 거쳐서만 쓴다.
  - `upload` → `editor`, `hooks`, `core`
  - `editor` → `core`, `takes`(2차 작업 중, 커밋 전), `captions`
  - `style` → `core`, `plan` · `qa`·`hooks` → `core`
  - `plan` → `core`. `style`·`avmodels`·`face`·`source`·`claude_cli`는 함수 안에서 지연 import한다 (`style`이 `plan`을 import하므로 순환을 피함).
  - `avmodels` → `core`, `thumb`(모델 받기 `fetch_model`) · `claude_cli` → (표준 라이브러리만). `app`이 `claude_cli`·`plan`을 직접 쓴다.
  - `takes` → (표준 라이브러리만). `editor`는 함수 안에서 지연 import한다.
  - `thumb` → `core`, `updater`. `face`·`detect`·`avmodels`·`thumbcopy`·`editor`는 함수 안에서 지연 import한다.
  - `face`·`detect` → `core`, `thumb`(모델 받기 `fetch_model`)
  - `thumbcopy` → `core`, `hooks`. `editor`·`claude_cli`·`updater`는 함수 안에서 지연 import한다. `app`이 `thumbcopy`를 직접 쓴다(문구·판정 작업).
  - `bundle` → `core`. `editor`는 함수 안에서 지연 import한다.
  - 지연 import는 순환을 피하려는 기존 예외다. 새로 추가하면 이유를 주석으로 남긴다.
- 하위 레이어는 상위 레이어를 import 하지 않는다.
- 순환 의존이 생기면 구현을 멈추고 구조를 먼저 보고한다.
- 레이어를 건너뛰는 접근(예: UI에서 DB 직접 호출)은 금지. 예외가 필요하면 `DECISION_LOG.md`에 기록 후 진행.

## 4. 주요 모듈

| 모듈 | 위치 | 역할 |
|---|---|---|
| app | `app.py` | HTTP 서버·라우팅, `start_job`(작업 하나씩, 겹치면 409), `log()`, pywebview 창·닫기 전 저장, 이미 켜져 있으면 그 창을 앞으로(`/api/focus`), 바로가기 만들기, 재시작 |
| updater | `updater.py` | `--launch`(업데이트 확인 → `run_app`), `install`(zip 검사 → staging → 버전·sha256 → 문법·selftest → 백업 → 교체 → 지울 파일 정리), `rollback`, `check`/`finish`(새 버전 import 확인·알림), `.update_skip` |
| core | `core.py` | `list_videos`(조회수 순), `download`(받는 폴더·archive·진행 이름·출처 기록 함수를 바꿀 수 있음 · 학습용 영상이 씀), 이름 → 파일·분석 폴더(`video_file`·`adir`: 보관함 먼저, 없으면 학습용 영상 · `kept_sig`), `analyze`(whisper 한국어 → transcript.json·analysis.json·subtitles.srt·timeline.md), 엔진 관리(`update_engine`·`engine_autoupdate` 3일·`ensure_deno`), `check_update`·`update_app`(pip는 요구사항이 바뀔 때만). `render`(컷 목록 → mp4 + EDL)와 `/api/render`는 현재 화면에서 부르지 않는 예전 기능이다 |
| editor | `editor.py` | `probe`(ffmpeg 출력 파싱), 파형·썸네일 줄·미리보기(proxy), `recommend`(규칙 기반: 추임새·반복·무음 정리 tidy, 쇼츠 구간), `auto_sequences`(롱폼 가편집 + 쇼츠 1~3, 스타일 값 적용), 프로젝트 load/save(rev 충돌 검사·백업·복구·마이그레이션), `reanalyze_project`, `export`(ffmpeg 렌더·HW 인코더·Premiere XML·SRT·취소) |
| thumb | `thumb.py` | `frame_candidates`(v6: 선명도·밝기 × 흔들림 × 얼굴·표정(상한 2.2) × 액션(선수·공) × 머리 잘림 × 장면 표시 `scene_flags`(벤치·관중·뒷모습·끝에 걸림·작음·레슨 액션), 박힌 글자 상자 `text_boxes`·감점, 같은 화면 dHash 3장·클로즈업 6장까지, 후보 16개 · 장면마다 `kind`·`persons`·`ball`·`main`·`flags`·`band`·`tboxes`·자동 보정 `grade`(뽑힌 장면만), 캐시 `candidates6.json`), `analyze`(클로드 문구(동시에) → 장면 → 클로드 장면 고르기 `run_ai_frames`(동시에) → 주인공 자동 누끼 4장(서로 다른 장면) → 문구, `cached_analysis`), `auto_grade`, `grab`, `remove_bg`·`cut_auto`(누끼 ONNX → `clean_mask` 다듬기 → `cut_quality` 품질, 옆 JSON), `read_text`(검수 OCR), 브랜드 키트 `load_brand`·`save_brand`(`thumbnails/brand.json`), `export_ab`(A/B 묶음 + 모바일 비교), 디자인 저장(`.bak`)·이미지 내보내기, `fetch_model`(크기·sha256 확인 후 제자리에 둠) |
| face | `face.py` | UltraFace 얼굴 + FER+ 표정 점수. `ensure()`가 처음에 모델을 받고, 실패하면 10분 동안 다시 시도하지 않고 조용히 False를 돌려줌 |
| detect | `detect.py` | YOLOX-nano(D-025)로 사람·공 찾기 `people(rgb)` → {persons[[x,y,w,h,확률]], ball}. `ensure`·`ready`(face 와 같은 실패 규칙), 레터박스·격자 해석·NMS |
| thumbcopy | `thumbcopy.py` | 썸네일 제목 문구: 주제어(`topics`)·규칙 틀(`rule_candidates` — 무조건 봐·비밀·못하는 진짜 이유·수비를 속이는 X·반전 O/X·대사 핵심 문장 …)·점수(주제가 작은 줄이면 감점)·다양하게 고르기(`suggest`), 사용자 클로드로 더 만들기(`run_ai` → `analysis/<stem>/thumb_copy.json`, 분석 때 자동 · `ai_ready`), 검수 창 평가(`judge`) |
| style | `style.py` | `analyze_style`(컷·줌·자막 띠·무음·LUFS·말 빠르기), `merge`(여러 레퍼런스 평균), `edit_params`(→ `auto_sequences`의 style 인자, 기획 분석이 있으면 인트로 티저·강조 자막 값도), `learn`(영상마다 `plan.extract_plan`·`judge` → `plan`)·`list_styles`, `style_file`·`update_style`(다른 값은 그대로 두고 바꿔 끼우기) |
| plan | `plan.py` | 영상 기획 분석 (D-021). `extract_plan`(1초 한 장 640px 지문·복잡도·잔디·화면 글자 OCR(상한 400장)·소리 종류·화자 수 → `plan_events.json`), `detect`(티저·타이틀·정지·리플레이·삽입·흔들기·몽타주·웃음·효과음·펀치라인 줌·자막 사건 6종), `judge`(인트로·장르·형식·자막·재미 판단 문장 + 확신 + 근거 1~2개 · 채널 공식 한 문장 `headline`), `merge_plans`(길이×확신 투표 · 갈리면 `mixed` 표시), `plan_params`(절반 넘게 같은 판단일 때만 가편집 값), `claude_prompt`·`parse_ai`·`run_ai`(Claude 판단 저장) |
| avmodels | `avmodels.py` | 기획 분석 모델: PP-OCRv5 글자 찾기·한국어 읽기, YAMNet 소리 종류. `ensure`(처음에 받기 · ✕ 로 멈춤, 실패하면 10분 쉬고 조용히 False · 불러오지 못한 파일은 지워 다시 받게), `usable`(기획 기록 재사용 판단), `ocr`, `tags` |
| claude_cli | `claude_cli.py` | 사용자 PC의 Claude Code CLI(사용자 클로드 계정)로 판단 받기: 실행 파일 찾기·`--help` 옵션 확인·`auth status`·보이는 창으로 설치/로그인·로그인 코드 저장·`run`(빈 임시 폴더, Read만, stdin, 제한 시간·멈추기, 한국어 오류) |
| qa | `qa.py` | `check_video`: 규격(16:9 / 9:16)·쇼츠 길이·검은 화면·멈춘 화면·소리 끊김·LUFS·피크를 점수로 |
| bundle | `bundle.py` | `make_bundle`: 촬영 시각 순서로 정렬해 같은 규격이면 그대로 이어 붙이고(copy concat), 다르면 다시 인코딩. 원본은 보존. 끝나면 app이 이어서 편집점 찾기 |
| upload | `upload.py` | `build_kit`·`save_edits`·`load_kit`: 제목 후보·설명(템플릿)·챕터·태그·썸네일 확인 → `out/<…>_올리기.txt/.json`. 유튜브 글자 수·챕터 규칙 적용 |
| hooks | `hooks.py` | 우리 채널 목록을 `channel_cache.json`에 기억, 제목 틀·풋살 주제어(`TERMS`)·조사 처리로 제목 후보 생성 |
| takes | `takes.py` (2차 작업 중, 커밋 전) | `find_junk`·`is_slate`: 받아쓰기 구간만 보고 NG 테이크·슬레이트 말·말더듬 구간을 규칙으로 찾음 → `editor.recommend`가 가편집·쇼츠 후보에서 뺌 (`KNOWN_ISSUES.md` I-012). `find_fillers`: 단어 시각이 있으면 홀로 떨어진 추임새 단어 |
| source | `source.py` | 보관함 영상의 출처(풋살사관학교·다른 채널·내 촬영본·모름) 판단과 기록(`videos/sources.json`, D-020). 받을 때 yt-dlp 채널 정보 기록, 예전 영상은 채널 목록 기억 → 뒤에서 천천히 영상 정보 조회(`start_backfill`), 직접 고르기(`set_manual`), 다른 채널은 채널별 묶음·고정 색(`channels`), 고르기 칩 개수(`summary`) |
| refs | `refs.py` | 학습용 영상(스타일 배우기 전용) 보관함 (D-022): 기록 `refs/refs.json`(바꿔 끼우기·잠김 재시도·깨지면 `.bad`), 채널별 폴더·색(`source._register` 재사용, 보관함과 같은 색), `add_channel`(인기 영상 N개 → `core.download`를 받는 곳만 바꿔 → 채널 폴더 → '<채널명> 스타일' 배우기), `add_direction`(추천 방향 A/B/C), `prune`(배운 파일만 지우고 지문 `sig` 남김), `delete`(원본은 한 번 더 확인 · 빈 채널 폴더만 지움), `move_from_library`(보관함 → 학습용: 편집실 프로젝트에서 쓰는 영상은 건너뜀 `projects_using` · 분석 폴더 → 영상 → 기록, 실패하면 되돌림 · `archive.txt`에서 id 뺌), `restore`(학습용 → 보관함으로 되돌리기), `listing`(기록 없이 refs 하위 폴더에 있는 파일·받는 폴더에 남은 파일은 다시 기록 `_adopt`), `recommended`(`ref_channels.json`) |
| captions | `captions.py` (2차 작업 중, 커밋 전) | 용어 사전(`dict.json` 읽기·쓰기, 받아쓰기 힌트 `prompt`·`hotwords`, 힌트를 따라 쓴 구간 찾기 `echo`), 낱말 경계 고치기(`apply_dict`·`fix_words`), 자막 나누기(`chunk`·`from_segments`, BR-013) |
| 화면 | `ui.html` · `editor.html` · `thumb.html` | 스튜디오 7단계(소재 찾기·보관함·편집점·편집실·썸네일·스타일 배우기·올리기), 편집실(`/api/edit/*`), 썸네일(`/api/thumb/*` · 원본 `thumb_src/parts/p1~p9` — p1 렌더러(자동 보정·전술 도형·맞춤 상자), p8 'AI 추천 썸네일'(레퍼런스형 템플릿 `T_NEW` 롱폼 11·쇼츠 8 + 예전 `TPL.long` 7·`TPL.short` 5(새 템플릿이 모자랄 때만), 배경 놓기 `frameLayer`(머리 지키기·박힌 글자 피하기)·`topLayouts`·`framesBelow`, 점수 `scoreDoc`(머리 잘림·가림 게이트, 장면 품질 `frameQ`), 고르기 `recommend`, 개발용 `window.__thumbAuto`), p9 전술 그래픽 메뉴·점 핸들·스티커·브랜드 키트·검수(모바일 미리보기·OCR·클로드 평가)·A/B 묶음) |

## 5. 데이터 모델 요약

모든 것의 키는 **보관함 영상의 파일 이름**(`videos/<이름>`)이다. 영상별 폴더와 파일 이름은 `core.adir(name).name`(확장자를 뺀 이름, 끝의 공백·점 제거)을 쓴다.

- 영상 1 : 1 분석 폴더 `analysis/<stem>/`
  - `transcript.json` [{start, end, text, words[{w, s, e, p}]}] (예전 받아쓰기는 `words` 없음), `analysis.json` {silences, loud_peaks}
  - `subtitles.srt`, `transcript_timeline.md`, `waveform_50.json`, `thumbs2.jpg/json`
  - `frames/`(장면 캡처, `candidates6.json`), `thumb_copy.json`(클로드 문구, 받아쓰기·제목 지문), `thumb_frames_ai.json`(클로드 장면 점수, 후보 장면 시각 지문), 묶음 영상이면 `bundle.json`
- 영상 1 : 1 편집 프로젝트 `projects/<stem>.json` = {source, info, captions[{id, start, end, text, words?}], sequences[], active, media[], v: 2, rev}
  - 프로젝트 1 : N 시퀀스(편집본) = {id, name, format: `long`|`shorts`, tracks[V1~3, A1~3], items[](V/A 클립: media·start·in·out·speed·link·fx 키프레임), trans[], markers[], titles[], shapes[], captionStyle, layout, master{volume, normalize, lufs}, duck, auto: `rough`|`style`}
  - 백업: `projects/backup/<stem>__YYYYMMDD_HHMMSS[_태그].json`. 자동 백업은 5분마다 최근 10개, 태그 백업(재분석전·덮어쓰기전·변환전)은 따로 10개
- 영상 1 : 1 썸네일 문서 `thumbnails/<stem>.json` {designs[]} (+ `.bak`). 이미지는 `thumbnails/assets/`(캡처·누끼·자동 누끼 `cut_auto_<키>.png` + 품질 `.json`·올린 그림). 브랜드 키트 하나 `thumbnails/brand.json` {logo, logoPos, colors{hl, hl2, accent, neon, box}, font, series, seriesOn, handle(예전 값 · 화면에서 안 씀), apply, aiCopy(분석 때 클로드 문구·장면 고르기)}
- 스타일 N : M 레퍼런스 영상: `styles/<이름>.json` = 합친 프로필(cutsPerMin·avgShot·medianShot·zoomCutsPerMin·avgZoom·pauseP75·captionRatio/Pos/Color·lufs·charsPerSec) + `refs[]`(영상별 프로필) + `plan`(영상 기획 분석: intro·genre·format·captions·fun·summary·apply·agree, Claude 판단 `ai`) · `refs[i].plan`(영상마다). `plan`이 없는 예전 파일도 그대로 읽는다
  - 영상마다 `analysis/<stem>/style_events.json`(구조, D-015)과 `plan_events.json`(기획 신호: 판 `v`·파일 `sig`·그때의 모델 상태·지문·OCR 줄·소리 점수·화자 수)을 따로 둔다
- 결과물 `out/`: `<stem>_<편집본>.mp4/.srt/_premiere.xml`(같은 이름이 있으면 ` (2)`…), 썸네일 `<stem>_<라벨>_<n>.jpg/png`·A/B 묶음 `<stem>_썸네일_A.jpg`…`_모바일 비교.jpg`(같은 이름이 있으면 ` (2)`), 올리기 키트 `<…>_올리기.txt/.json`, 렌더 임시 폴더 `.render_*`(켤 때 정리)
- 학습용 영상(스타일 배우기 전용, D-022) `refs/<채널 폴더>/<영상>` · 분석 기록 `refs/<채널 폴더>/_analysis/<stem>/`(style_events·plan_events·옮겨 온 받아쓰기) · 받는 중 `refs/_받는 중/`. 기록 `refs/refs.json` = {channels{채널 열쇠: {name, names, handles, url, color, folder}}, files{파일 이름: {channelKey, folder, videoId, title, how(download·move·found), kind, saved, pruned, sig, original·origin(확인하고 옮긴 원본 · 자동으로 지우지 않음)}}, ids{}, ui{bannerDismissed}}. 채널 열쇠·색 규칙은 `sources.json`과 같다. 같은 이름이 보관함에도 있으면 보관함이 먼저다(`core.video_file`·`adir`). 보관함에 파일이 없으면 옛 `analysis/<stem>`이 남아 있어도 학습용 기록이 먼저다. channels 의 `original`: '풋살사관학교'·'내 촬영본' 칸(원본)
- 보관함 출처 기록 `videos/sources.json` = {files{파일 이름: 기록}, ids{영상 id: 기록}, channels{채널 열쇠: {name, names, handles, url, color}}, own{ids, handles}(배운 우리 채널), lookup{영상 id: 조회 실패 시각·이유}}. 기록 = 채널 정보(channel·channelId·channelUrl·uploaderId·uploaderUrl)·kind·how(download·lookup·listing·cache·local·bundle)·manual·channelKey·manualChannel. 깨지면 `sources.json.bad`로 남기고 빈 기록으로 계속 (D-020)
- 그 밖에 `edit_media/`(편집실로 가져온 음악·이미지·영상·정지 화면), `analysis/_media/`(가져온 미디어 캐시), `channel_cache.json`, `upload_template.txt`, `dict.json`(용어 사전 {terms, fix, v}, 없으면 기본 사전), `studio.log`
- 사용자별 `~/.futsal-studio/`: `bin/deno.exe`, `models/*.onnx`(+ OCR 글자 목록 `ocr-ppocrv5-korean-dict.txt`), `claude_token`(선택: 사용자가 붙여 넣은 클로드 로그인 코드), `engine_upgrade.json`·`deno_install.json`(엔진을 마지막으로 바꾼·실패한 시각)
- 앱 폴더의 업데이트 상태: `.update_staging/`, `.rollback/v<이전>/{files/, rollback.json}`, `.update_pending`, `.update_result`, `.req_hash`, `.update_skip` (모두 `.gitignore`에 있음)
- 규칙·불변식(예: 다시 분석해도 사용자 편집본을 덮어쓰지 않음, `config.json`을 보존함)은 `DOMAIN_KNOWLEDGE.md` 참고

## 6. 횡단 관심사 처리 방식

- **에러 처리**:
  - 기능 모듈은 **사용자에게 그대로 보여 줄 한국어 메시지**로 예외를 던진다. 예: `RuntimeError`, `updater.UpdateError`, `editor.Conflict`.
  - 작업 안에서 난 예외는 `JOB.error`와 `log("문제가 생겼어요 · …")`를 거쳐 화면에 표시된다. 바로 응답하는 API는 `Handler`가 4xx/5xx + `{"error": …}`로 바꾼다.
  - 여러 항목을 처리할 때는 하나가 실패해도 나머지를 계속한다(다운로드·스타일 배우기).
  - 선택 기능(얼굴 모델·Deno·엔진 자동 최신화)은 실패해도 멈추지 않고 대체 경로로 간다.
- **로깅**:
  - `app.log()`는 메모리 `LOG`(화면 '작업 기록', `/api/state?since=`), 작업 폴더 `studio.log`(`MM-DD HH:MM:SS` 접두어), stdout에 한꺼번에 남긴다.
  - `updater.studio_log()`는 앱이 안 켜져도 같은 `studio.log`에 남긴다.
  - 진행률은 `core.set_progress(label, item, step, pct, detail)`로 알리고 `/api/state`의 `progress`로 전달된다.
  - 사용자 PC에서 문제가 생기면 `studio.log`를 받는다.
- **설정/환경변수 접근**:
  - `config.json`은 `core.CONFIG`(import 때 한 번 읽음)와 `updater.workspace()`(core 없이 같은 규칙)만 읽는다.
  - 경로는 모듈 상수로 쓴다: `core.WORK/VIDEOS/ANALYSIS/OUT`, `editor.PROJECTS/ASSETS`, `thumb.THUMBS/ASSETS/MODELS`, `style.STYLES`.
  - 환경변수는 `FUTSAL_*` 몇 개뿐이다(`PROJECT_CONTEXT.md` 5절).
  - 업데이트는 `config.json`을 덮어쓰지도 지우지도 않는다(`updater.KEEP`). 파일이 없을 때만 넣는다.
- **인증/인가 체크 위치**: 로그인은 없다(로컬 1인용). 대신 `Handler`가 모든 요청에서 다음을 확인한다.
  - Host 헤더가 `127.0.0.1:PORT`/`localhost:PORT`인지 확인한다(DNS 리바인딩 차단).
  - POST는 Origin이 같은 출처인지 확인한다.
  - 영상·파일 이름 인자는 `editor.safe_name()`/`video_path()`로 경로·드라이브·`..`를 거절한다.
  - 파일을 내줄 때는 `resolve()` 후 허용 폴더 안인지 확인한다.
  - 서버는 `127.0.0.1`에만 bind한다.
- **동시성**:
  - 긴 작업은 `JOB` 하나다. 겹치면 409와 함께 "다른 작업이 끝난 뒤에 다시 눌러 주세요"를 돌려준다. 단 `/api/thumb/frames`·`/api/thumb/analyze`는 캐시가 있으면 바로 응답한다 (분석이 없으면 작업 '썸네일 분석', 클로드 문구·평가도 작업 하나).
  - 저장은 모듈별 잠금으로 보호한다: `editor._SAVE_LOCK`, `thumb._SAVE_LOCK`, `upload._LOCK`.
  - 다운로드 엔진은 `core._ENGINE_LOCK`(pip 중에만)과 `_DENO_LOCK`으로 보호한다.
  - 멈추기(✕)는 `editor.CANCEL`, `run_killable`, `cancel_export`로 처리한다.
- **파일 저장 안전**:
  - 저장 순서: 임시 파일에 다 쓴다 → `os.replace`로 바꾼다. Windows 잠금에 대비해 재시도한다(`updater._replace`·`editor._replace_retry`).
  - 편집 프로젝트는 `rev` 판 번호로 다른 창의 덮어쓰기를 막고(409 conflict), 백업한다.
  - 깨진 파일은 지우지 않고 옆에 `.bad`로 남긴 뒤 최근 백업으로 복구한다.
  - 썸네일 문서는 `.bak`을 남긴다.
- **외부 프로세스·미디어 정보**:
  - 외부 프로세스는 `core.run()`으로 실행한다(Windows `CREATE_NO_WINDOW`).
  - ffprobe가 없으므로 미디어 정보는 `ffmpeg -i`의 stderr를 파싱해 얻는다(`editor.probe`, `bundle.probe`, `style._probe_duration`).
  - ffmpeg는 `core.ffmpeg()` 하나로만 찾는다.
- **업데이트 흐름**: 받기 → `testzip` → `.update_staging`에 풀기 → 버전·sha256 확인 → 모든 `.py` 문법 확인과 새 `updater.py --selftest` → 바뀔 파일을 `.rollback/v<이전>`에 복사 → `os.replace`로 교체(재시도) → manifest에서 빠진 파일 삭제(`config.json`·`.venv`·작업 폴더는 제외) → `.update_pending` 기록 → (core) 요구사항이 바뀌었으면 pip(`.req_hash`) → 재시작. 다음 실행 때 실행기는 다음과 같이 처리한다.
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
8. 사용자가 만든 데이터(편집본·썸네일 디자인·설정)는 덮어쓰지 않는다. 다시 만드는 결과는 옆에 추가하고, 바꿀 때는 먼저 백업한다. 상세 규칙은 `DOMAIN_KNOWLEDGE.md`에 있다.
