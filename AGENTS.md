# AGENTS.md — AI 에이전트 작업 지침 (진입점)

> 이 저장소는 **1인 개발자가 AI와 협업하여 커뮤니티 서비스 및 프로그램을 개발**하는 프로젝트다.
> 이 파일은 AI 코딩 에이전트(Claude Code, Codex, Cursor 등)가 작업 시 가장 먼저 읽는 진입점이며,
> 세부 규칙은 `docs/` 아래 문서로 분리되어 있다.
>
> **도구별 파일명 주의**: Claude Code는 `CLAUDE.md`를 읽는다. 이 저장소의 `CLAUDE.md`는
> 본 파일을 가리키는 포인터다. 지침 수정은 항상 이 파일과 `docs/`에서만 한다.

---

## 0. 협업 원칙 — 역할과 판단 권한

이 프로젝트의 의사결정 구조는 단순하다. **소유자(개발자)가 결정하고, AI는 실행한다.**

- **판단 권한은 소유자에게 있다.** 기능의 타당성, 제품 방향, 사업적·윤리적 적절성, 법적 준수 책임, 실행 여부의 최종 판단은 모두 소유자의 몫이다. AI는 이 영역에서 결정을 대신하려 하지 않는다.
- **AI의 역할은 실행과 정보 제공이다.** 우려나 리스크가 보이면 **한 번, 한두 문장으로 간결하게** 알린 뒤 소유자의 결정을 따른다. 같은 우려를 반복하거나, 설교하거나, 장황한 경고문을 붙이지 않는다.
- **"가능/불가"는 시도로 증명한다.** 어려워 보인다는 이유로 거절하거나 축소 제안하지 않는다. 우선 지시받은 대로 시도하고, 기술적으로 불가능할 때만 "무엇이, 왜 불가한지 + 가장 가까운 대안"을 보고한다.
- **지시받은 작업이 항상 최우선이다.** 소유자가 말한 작업을 먼저 완료한다. 그 과정에서 발견한 개선거리는 작업을 끝낸 뒤 제안 목록으로만 전달한다.
- 단, AI 플랫폼 자체의 안전 기준(명백한 불법 행위 등)은 이 문서로 변경되지 않는다. 그 외의 모든 회색지대 판단은 소유자에게 넘긴다.

## 1. 프로젝트 한 줄 요약

**풋살사관학교 스튜디오**는 유튜브 채널 '풋살사관학교'(최경진 감독과 50/50 파트너)를 되살리기 위한 **Windows 전용 로컬 영상 제작 앱**이다.
- 구성: Python 표준 라이브러리 HTTP 서버 + pywebview 창 + 단일 파일 HTML 화면. 영상 처리에 ffmpeg·yt-dlp·faster-whisper·onnxruntime을 쓴다.
- 흐름: 영상 받기 → 받아쓰기·편집점 → 자동 가편집(롱폼 16:9·쇼츠 9:16) → 편집실 → 썸네일 → 올리기 키트까지 한 앱에서 한다.
- 최종 목표: 유명 유튜버의 편집 스타일을 배워 스스로 편집하는 도구.

- 사용자는 기술을 잘 모르는 한국인이고 **Windows PC만** 지원한다. 화면 문구는 쉬운 한국어 존댓말(해요체)로 쓴다.
- `release.sh`로 배포한 커밋은 사용자 PC에 자동 업데이트로 그대로 설치된다(`tests/`를 뺀 모든 추적 파일). `updater.py`·`release.sh`·`config.json`·`thumb.html`을 건드리기 전에는 `docs/ARCHITECTURE.md` 7절을 먼저 읽는다.
- 상세: `docs/PROJECT_CONTEXT.md`

## 2. 문서 맵 — 상황별로 읽어야 할 문서

| 상황 | 읽어야 할 문서 |
|---|---|
| 프로젝트가 처음이거나 맥락이 필요할 때 | `docs/PROJECT_CONTEXT.md` |
| 구조 변경, 새 모듈/레이어 추가 | `docs/ARCHITECTURE.md` |
| 모든 코드 작업 공통 (브랜치·커밋·작업 범위) | `docs/DEVELOPMENT_RULES.md` |
| 코드를 작성/수정할 때 | `docs/CODING_STANDARDS.md` |
| 테스트를 작성/수정/실행할 때 | `docs/TESTING_GUIDELINES.md` |
| 기존 코드를 리팩토링할 때 | `docs/REFACTORING_GUIDELINES.md` |
| 로컬 서버 API·파일 경로·다운로드·업데이트·쿠키·개인정보·시크릿을 다룰 때 | `docs/SECURITY_GUIDELINES.md` |
| 라이브러리/패키지를 추가·업데이트할 때 | `docs/DEPENDENCY_POLICY.md` |
| 문서를 작성·갱신할 때 | `docs/DOCUMENTATION_POLICY.md` |
| AI 작업 절차 전반 (계획→구현→검증→기록) | `docs/AI_WORKFLOW.md` |
| 중요한 기술 결정을 내리거나 과거 결정을 확인할 때 | `docs/DECISION_LOG.md` |
| 버그·제약·임시방편을 만나거나 남길 때 | `docs/KNOWN_ISSUES.md` |
| 도메인 용어·서비스 규칙이 헷갈릴 때 | `docs/DOMAIN_KNOWLEDGE.md` |

전부 매번 읽을 필요는 없다. 작업 유형에 해당하는 문서만 읽되,
`DEVELOPMENT_RULES.md`와 `AI_WORKFLOW.md`는 모든 작업의 공통 전제다.

## 3. 절대 규칙 (Golden Rules)

1. **지시받은 범위만 수정한다.** 스코프 밖 파일의 "김에 하는 개선"은 금지. 발견한 문제는 `docs/KNOWN_ISSUES.md`에 기록하고 보고만 한다.
2. **파괴적 작업만 사전 확인한다.** 파일/브랜치/데이터 삭제, 히스토리 변경(force push), 운영 DB 마이그레이션, 배포는 명시적 승인 후 수행. 그 외 사소한 결정은 묻지 말고 합리적 기본값으로 진행하되 가정을 결과 보고에 명시한다.
3. **시크릿을 코드·로그·문서에 절대 남기지 않는다.** API 키, 비밀번호, 토큰 발견 시 즉시 보고한다.
4. **검증 없이 완료 선언 금지.** 변경 후 빌드·테스트·린트를 실제 실행해 통과를 확인한 뒤 완료로 보고한다. 실행 불가 시 그 사실을 명시한다.
5. **새 의존성은 임의로 추가하지 않는다.** `docs/DEPENDENCY_POLICY.md` 기준을 따른다.
6. **모호하면 가장 합리적인 해석으로 진행하고 해석을 명시한다.** 해석이 크게 갈리는 경우(공수 차이가 큰 경우)에만 짧게 확인한다.
7. **중요 결정은 `docs/DECISION_LOG.md`에 기록한다.**
8. **기존 코드 컨벤션이 문서와 충돌하면 기존 코드를 따르고** 충돌 사실을 한 줄 보고한다.

## 4. 작업 기본 흐름 (요약)

`이해 → 계획 → 구현 → 검증 → 기록` — 상세 절차는 `docs/AI_WORKFLOW.md` 참고.

- 작업 전: 관련 문서 + 관련 코드를 먼저 읽는다.
- 3단계 이상 작업은 할 일 목록을 먼저 제시하고 진행한다.
- 구현은 작은 단위로 나누고, 단위마다 검증한다.
- 작업 후: 영향받은 문서(`DECISION_LOG`, `KNOWN_ISSUES` 등)를 갱신하고, 결과를 간결하게 보고한다.

## 5. 빌드·테스트·실행 명령어

모든 명령은 저장소 루트에서 실행한다. 개발 PC(Linux)의 `python3` 기준이다.

- **의존성 설치**: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt` — 아래의 `python3`는 이 venv를 켠 셸 기준이다(리드 개발 PC는 시스템 Python에 이미 설치돼 있음).
  - 사용자 Windows PC에서는 `시작하기 (Windows).bat`이 같은 일을 하고 실행까지 한다.
- **앱 실행 (개발)**: `python3 app.py --browser` — 전용 창 없이 서버를 띄우고 브라우저로 `http://127.0.0.1:8765/`를 연다(편집실 `/editor?name=…`, 썸네일 `/thumb?name=…`).
  - 포트는 `FUTSAL_PORT=8766`처럼 바꾼다.
  - 저장소의 `config.json`은 배포 파일이라 고치지 않는다. 테스트 작업 폴더가 필요하면 저장소를 복사한 폴더의 `config.json`만 바꾼다.
- **사용자와 같은 실행 경로**: `python3 updater.py --launch` (업데이트 확인 → 같은 프로세스에서 app.py 실행)
- **단위 테스트 (전체, 약 1,455개)**: `python3 -m unittest discover -s tests`
  - 파일별: `python3 -m unittest tests.test_<영역>` (파일 목록은 `docs/TESTING_GUIDELINES.md` 1번). `tests.test_update`는 배포 전 필수이고 `release.sh`가 자동으로 돌린다.
- **실행기 자가 시험**: `python3 updater.py --selftest` → `selftest ok`
- **린트**: 설정 파일은 없다. `python3 -m pyflakes *.py` — 새 경고가 없어야 한다(지금 남은 경고는 `docs/KNOWN_ISSUES.md` I-019). 고친 HTML의 JS는 문법만 확인한다(`docs/CODING_STANDARDS.md` 3번). pyflakes가 없으면 최소 `python3 -m py_compile *.py`.
- **저장소 밖 작업 공간 `$SCRATCH`**: 썸네일 원본·빌드 스크립트·e2e 묶음은 저장소가 아니라 리드 개발 환경의 scratchpad에 있다(`docs/KNOWN_ISSUES.md` I-018). 작성 시점 경로는 `/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad`이고 세션·환경마다 바뀔 수 있다. 다른 문서는 이 경로를 `$SCRATCH`로만 적는다.
- **E2E (Playwright)**: 편집실 `$SCRATCH/ed2_test.py`(193개 확인) · 창 크기별 배치 `$SCRATCH/edlayout_e2e.py`(`ed2_clone.sh` 폴더 + `LAY_DIR`·`LAY_PORT`·`LAY_REPO`), 썸네일 `$SCRATCH/th2_test.py` · AI 추천 썸네일 `tests/e2e/thq_e2e.py`(시험 작업 폴더는 `tests/e2e/mkwork.py` · `docs/TESTING_GUIDELINES.md` 1번), 스타일 배우기 `$SCRATCH/style_test.py`(기획 분석·클로드 카드는 `bash $SCRATCH/plan_run.sh` 시험 앱 8911 + `STYLE_PORT=8911 PLAN_DIR=$SCRATCH/plantest`), 학습용 영상 `bash $SCRATCH/refs_e2e/run.sh`(8931) → `python3 $SCRATCH/refs_e2e/refs_ui_test.py <폴더>`, 자막 한 줄씩 나누기 `bash $SCRATCH/cape2e/setup.sh` → `python3 $SCRATCH/cape2e/cap_e2e.py`(8951·8952, 저장소는 `CAP_REPO`), 채널 전략 `bash $SCRATCH/strat_e2e/run_all.sh` → `python3 $SCRATCH/strat_e2e/strat_ui_test.py <폴더>`(8981~8983, 저장소는 `STRAT_REPO` · 8984 는 인터넷으로 실제 새로 고침 `live_ui.py`), 휴대폰으로 보기 `python3 $SCRATCH/remote_e2e/remote_ui_test.py`(8971~8974, 저장소 `REMOTE_REPO` · 휴대폰 페이지 `FUTSAL_SITE_DIR`, 기본 `/home/user/wavely/public/futsal` — 와벨리 저장소), 유튜브에 바로 올리기 `bash $SCRATCH/yt_e2e/run_all.sh <폴더>`(가짜 Google 8944 · 시험 앱 8945, 저장소는 `YT_REPO` · 인터넷 안 씀), 복사 중·실패 카드·로그인 정보 브라우저·MP4로 바꾸기 `bash $SCRATCH/e1fix_ui/setup.sh <이름> <포트>` → `python3 $SCRATCH/e1fix_ui/ui_test.py <그 폴더> <포트> <스크린샷 폴더>`(가짜 YouTube · 시스템 ffmpeg · 저장소는 `REPO` · 예전 판 `e1a_ui`), 편집실 노트북 배치 보강·실패 카드 `$SCRATCH/e1fix_ui/ed_test.py`(`ed2_clone.sh` 폴더 + `ED_DIR`·`ED_PORT`·`ED_REPO`), 받아쓰기 기본값·꺼짐 카드·이름 바꾸기·다음 할 일·마커 탭 `bash $SCRATCH/e10_ui/setup.sh a 9153` → `python3 $SCRATCH/me10_e2e/e10_ui_test.py <그 폴더> 9153 <스크린샷 폴더>`(저장소는 `REPO` · E10 원본 `e10_ui/ui_test.py` 에 [다음: 썸네일 만들기] 뒤 작업이 끝나길 기다리는 한 줄을 더한 사본 — 이 저장소에서는 원본이 장면 고르기 작업과 겹쳐 409 로 멈춤 · `docs/KNOWN_ISSUES.md` I-074), [썸네일 만들기] 첫 화면에 AI 추천 상자(합침 확인 · D-078) 같은 `setup.sh <이름> <포트>` 폴더로 `python3 $SCRATCH/me10_land/land.py <그 폴더> <포트> <스크린샷 폴더>`, 옛 이름 썸네일 창의 그림 저장·A/B 가 '저장 안 함'(D-078) 은 같은 방식으로 `python3 $SCRATCH/me10_land/gone.py <그 폴더> <포트> <스크린샷 폴더>`. 영향받는 화면을 고쳤으면 해당 묶음을 돌린다. 시험 앱 띄우기·실행 명령은 `docs/TESTING_GUIDELINES.md` 1번.
- **빌드**: 앱 자체는 빌드가 없다(파이썬·HTML을 그대로 배포). 예외는 `thumb.html` 하나다. `python3 thumb_src/build.py`가 저장소 안 `thumb_src/head.html` + `thumb_src/parts/p1_core.js`…`p9_overlay.js`를 이어 붙여 같은 저장소의 `thumb.html`을 쓰고 node로 JS 문법을 확인한다(`syntax ok`). `thumb_src/`는 배포 파일 목록(manifest)에서 빠진다. 규칙은 `docs/ARCHITECTURE.md` 7절 6번.
- **배포**: `./release.sh X.Y.Z "변경 내용 한 줄"` — 절대 규칙 2에 따라 소유자 승인 후에만 한다. `git add -A`를 하므로 커밋 안 한 다른 작업이 있으면 깨끗한 클론에서 한다. 절차·주의는 `docs/DEVELOPMENT_RULES.md` 8번.
