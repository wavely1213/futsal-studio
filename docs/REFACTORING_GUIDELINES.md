# REFACTORING_GUIDELINES.md — 리팩토링 지침

> **목적**: 기존 코드를 안전하게 개선하기 위한 규칙. 리팩토링은 **동작을 바꾸지 않고**
> 구조만 개선하는 작업이며, 기능 변경과 절대 섞지 않는다.

---

## 1. 대원칙

1. **동작 보존**: 리팩토링 전후로 외부에서 관찰되는 동작이 동일해야 한다. 이 앱에서 "외부"는:
   - 화면 3개(`ui.html`·`editor.html`·`thumb.html`)가 쓰는 API 경로와 JSON 모양
   - 사용자 작업 폴더의 파일 이름·형식 (`projects/*.json` 과 백업, `styles/*.json`, 썸네일 디자인, `*_올리기.txt/.json`, `channel_cache.json`, `analysis/<영상>/` 의 `transcript.json`·`bundle.json` 등)
   - 내보낸 결과물 (영상의 화면·소리·싱크, 프리미어 XML, SRT, EDL)
   - 화면·`studio.log` 에 보이는 문구 (테스트가 문구를 확인하는 곳도 있다)
   - 업데이트 경로: `manifest.json` 형식, `updater.py --launch`·`--selftest`, `config.json` 키, `시작하기 (Windows).bat` — 예전 버전 PC 가 이것들에 기대고 있다
2. **테스트 먼저**: 테스트가 있는 코드만 리팩토링한다. 없으면 특성 테스트(characterization test)를 먼저 작성한다.
3. **작은 단계**: 큰 리팩토링도 각각 검증 가능한 작은 단계로 쪼갠다. 각 단계 후 테스트 통과 확인.
4. **기능 변경과 분리**: "리팩토링 + 기능 추가"를 한 커밋/한 작업에 섞지 않는다.

## 2. 언제 리팩토링하는가

- **소유자가 지시했을 때** — 기본 원칙. AI가 임의로 대규모 리팩토링을 시작하지 않는다.
- 지시받은 작업 수행에 **필요한 최소한의** 정리(예: 수정 대상 함수가 수정 불가능할 정도로 얽힌 경우)는 허용하되, 착수 전에 범위를 한 줄로 보고한다.
- "지나가다 발견한 더러운 코드"는 건드리지 않고 `KNOWN_ISSUES.md`에 기록한다.

## 3. 절차

1. **범위 선언**: 무엇을, 왜, 어디까지 바꿀지 명시 (대규모면 사전 승인).
2. **안전망 확인**: 대상 코드의 테스트 존재·통과 확인. 없으면 특성 테스트 작성.
   - 단위 테스트(`tests/`)가 있는 곳: `updater.py`, `core` 다운로드 엔진, `bundle.py`, `upload.py`·`hooks.py`, `face.py`·`thumb.frame_candidates`, `takes.py`·`editor.recommend`(2차 작업 중인 `tests/test_takes.py`)
   - e2e 묶음(저장소 밖, `TESTING_GUIDELINES.md` 1번)에만 기대는 곳: `editor.py`·`editor.html`, `thumb.html`·`thumb.py`, `style.py`, `qa.py` → 그 묶음을 돌릴 수 없으면 `tests/` 에 특성 테스트부터 만든다
3. **단계별 실행**: 이름 변경 → 함수 추출 → 이동 → 구조 변경 순으로, 한 번에 한 종류씩.
4. **각 단계 검증**: 단위 테스트 전체 통과(`python3 -m unittest discover -s tests`) + 해당 e2e 묶음 + 고친 `.py` 의 `pyflakes`·고친 HTML 의 JS 문법 확인. `updater.py` 를 건드렸으면 `python3 updater.py --selftest` 도.
5. **마무리**: 죽은 코드 제거, 문서 갱신(`ARCHITECTURE.md` 등), 결과 보고.

## 4. 판단 기준 (무엇이 "개선"인가)

- 중복 제거는 **세 번째 중복이 나타났을 때** (성급한 추상화 금지).
- 추상화는 실제 필요가 두 개 이상 있을 때만 도입한다.
- "더 짧은 코드"가 아니라 "더 읽기 쉬운 코드"가 목표다.
- 패턴 적용 자체가 목적이 되지 않는다 (디자인 패턴 강박 금지).
- 이미 세 번을 넘긴 중복 (정리는 지시가 있을 때만):
  - "임시 파일에 쓰고 Windows 잠금이면 다시 시도하며 `os.replace`" — `updater._replace`/`_write_json`, `editor._replace_retry`/`_write_atomic`, `bundle._replace_retry`, `hooks._write_json`, `upload._write_safe`, `thumb.save_docs`. (`youtube_upload._write_json`·`youtube_api.save_secret` 은 처음부터 `updater.write_atomic` 을 씀 · D-048) 재시도 횟수·간격·실패 때 동작(다른 이름으로 저장 등)이 서로 달라서 하나로 합치면 동작이 바뀔 수 있다. 합친다면 `updater.py` 쪽에 두고 다른 모듈이 가져다 쓰는 방향만 가능하다 (`updater.py` 는 다른 앱 모듈에 기대면 안 됨).
  - ffmpeg `Duration:` 읽기 — `editor.probe`, `style._probe_duration`, `qa.check_video`, `bundle.probe`.
- 짝으로 있는 계산(`editor.html` ↔ `editor.py`: 키프레임·전환 범위·트랙 상태·화면 배치·색보정)은 같은 단계에서 양쪽을 함께 바꾸고, 미리보기와 내보내기 결과가 같은지 e2e 로 확인한다.

## 5. 금지

- 테스트 없는 상태에서의 구조 변경 (안전망 먼저)
- 공개 API·DB 스키마 변경을 동반한 "리팩토링" — 이것은 기능 변경이며 별도 승인 필요. 이 앱에서는 API 경로·JSON 모양, 작업 폴더 저장 형식(바꾸면 `editor.migrate_project` 같은 변환과 형식 번호 `v` 가 필요), 모듈 파일 이름(바꾸면 `updater.IMPORT_CHECK`·배포 목록·테스트의 `mock.patch` 대상이 함께 바뀜)이 여기에 해당한다
- 리팩토링 중 발견한 버그의 즉석 수정 — 기록 후 별도 작업으로 분리 (동작 보존 원칙)
- 도구·프레임워크 교체 수준의 변경을 리팩토링으로 포장하는 것 (`DECISION_LOG.md` 대상) — 예: 단일 HTML 을 프레임워크·번들러로 쪼개기, 표준 라이브러리 서버를 웹 프레임워크로 바꾸기, `editor.py`·`editor.html` 을 여러 파일로 나누기
- `updater.py` 에 서드파티·다른 앱 모듈 의존을 들이는 것 (모든 실행이 거치는 문 — 망가지면 사용자 PC 에서 앱이 아예 안 켜진다)
- 모듈 수준 경로 상수(`core.WORK`·`VIDEOS`·`ANALYSIS`·`OUT`, `editor.PROJECTS`, `style.STYLES`, `thumb.THUMBS`·`ASSETS`·`MODELS`)를 다른 곳으로 옮기거나 import 때 값을 복사해 두는 것 — 테스트가 이 상수를 바꿔 끼워 임시 폴더를 쓴다
- 빌드 결과물 `thumb.html` 을 직접 정리하는 것 — 소스 조각에서 하고 다시 빌드한다 (`ARCHITECTURE.md` 7절 6번)
