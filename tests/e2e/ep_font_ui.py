"""은평 스타일 KFA 글꼴 고정 화면 e2e (BR-310~BR-313 · D-500~D-505 · 개발용 · 배포 안 됨 · unittest 가 줍지 않음) — 마지막 줄 'N/N 통과' 면 통과.

실행: EP_SRC=<시험 작업 폴더> [EP_REPO=<저장소>] [EP_PORT=9489] [EP_OUT=<출력 폴더>] python3 tests/e2e/ep_font_ui.py
- EP_SRC: videos/ 와 analysis/<영상 이름>/ 이 있는 작업 폴더 — MSG 시험 원본 MSGRAW01 (tests/e2e/ep_pics_ui.py 와 같은 폴더 · 리드 개발 환경은
  `$SCRATCH/topic_ev2/work` · 영상은 저장소에 넣지 않음)
- KFA 글꼴은 저장소에 없으므로 앱 글꼴 fonts/DoHyeon-Regular.ttf 를 'KFA고딕 테스트.ttf' 로 복사해 '글꼴 파일 고르기'로 고름 (대신할 글꼴)
- 저장소를 EP_OUT/app 으로 복사(config.json 은 시험용)해 띄우고, 작업 폴더 EP_OUT/work 는 매번 새로 · 스크린샷·비교 그림 EP_OUT/shots
확인: 스타일 화면 글꼴 줄(못 찾음 한 줄 · 고르기 · 고른 글꼴 이름 · 글꼴 아닌 파일 거절 · 처음으로) · 편집실 미리보기의 모든 글자가 그 글꼴(@font-face)로 그려짐 ·
큰 제목 크기 = 서버 셈(그 글꼴 폭) · 미리보기 = 내보낸 장면(1080×1920 · 흰 상자 큰 제목 글씨 자리) · 다른 템플릿은 예전 글꼴 · JS 오류 0."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("EP_REPO") or Path(__file__).resolve().parents[2])
SRC = Path(os.environ.get("EP_SRC", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/topic_ev2/work"))
OUT = Path(os.environ.get("EP_OUT", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/ep_font_e2e"))
PORT = int(os.environ.get("EP_PORT", "9489"))
CHROME = os.environ.get("EP_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
APP, WORK, SHOTS = OUT / "app", OUT / "work", OUT / "shots"
N1 = "20261008_MSGRAW01_퍼스트 터치 레슨.mp4"
res = []
sys.path.insert(0, str(REPO))


def check(name, ok, info=""):
    res.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  · {info}" if info else ""), flush=True)


def api(path, body=None, timeout=300):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


# ---- 시험 앱 · 작업 폴더 (이 시험이 띄운 앱만 끔) ----
for pid in os.popen("pgrep -f 'app.py --browser'").read().split():
    try:
        if os.readlink(f"/proc/{pid}/cwd").startswith(str(APP)):
            os.kill(int(pid), 15)
    except OSError:
        pass
time.sleep(1)
shutil.rmtree(WORK, ignore_errors=True)
shutil.rmtree(APP, ignore_errors=True)
(WORK / "videos").mkdir(parents=True)
src = (SRC / "videos" / N1).resolve()
try:
    os.link(src, WORK / "videos" / N1)
except OSError:
    os.symlink(src, WORK / "videos" / N1)
shutil.copytree(SRC / "analysis" / N1[:-4], WORK / "analysis" / N1[:-4], ignore=shutil.ignore_patterns("frames"))
SHOTS.mkdir(parents=True, exist_ok=True)
subprocess.run(f"cd '{REPO}' && tar --exclude=./tests --exclude=./.git --exclude=__pycache__ --exclude=./config.json -cf - . | (mkdir -p '{APP}' && cd '{APP}' && tar -xf -)",
               shell=True, check=True)
(APP / "config.json").write_text(json.dumps({"channel_url": "https://www.youtube.com/channel/UCRYziLOw2T6BF6fXtpUby-g", "workspace": str(WORK),
                                             "update_manifest_url": "http://127.0.0.1:1/none"}), encoding="utf-8")
srv = subprocess.Popen(["python3", "app.py", "--browser"], cwd=APP, env=dict(os.environ, FUTSAL_PORT=str(PORT), BROWSER="/bin/true"),
                       stdout=open(OUT / "app.log", "w"), stderr=subprocess.STDOUT)
for _ in range(120):
    try:
        api("/api/state")
        break
    except Exception:
        time.sleep(0.5)

FONT = OUT / "내 글꼴" / "KFA고딕 테스트.ttf"   # 대신할 글꼴 (Windows 의 '다운로드\\KFA고딕 테스트.ttf' 같은 파일)
FONT.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(REPO / "fonts" / "DoHyeon-Regular.ttf", FONT)
BAD = OUT / "내 글꼴" / "글꼴 아님.ttf"
BAD.write_bytes(b"not a font at all")
from PIL import Image, ImageFont  # noqa: E402
import numpy as np  # noqa: E402

try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("dialog", lambda d: d.accept())
        ev, W = pg.evaluate, pg.wait_for_timeout

        # ---- 1. 스타일 화면: 못 찾음 한 줄 → 글꼴 파일 고르기 → 고른 글꼴 이름 · 처음으로 ----
        pg.goto(f"http://127.0.0.1:{PORT}/")
        W(1500)
        pg.locator('[data-p="style"]').first.click()
        W(1200)
        ev("document.getElementById('cardFont').scrollIntoView({block: 'center'})")
        s0 = ev("({say: $('cardFontSay').textContent, reset: $('cardFontReset').hidden, pick: $('cardFontPick').textContent})")
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s1_font_not_found.png"))
        check("스타일 화면: 'KFA 글꼴을 이 PC에서 찾지 못했어요' 한 줄 · [글꼴 파일 고르기] · '처음으로' 숨김",
              s0["say"].startswith("KFA 글꼴을 이 PC에서 찾지 못했어요") and s0["reset"] and s0["pick"] == "글꼴 파일 고르기", json.dumps(s0, ensure_ascii=False))
        pg.select_option("#shortsLayout", "eunpyeong")
        W(800)
        pg.locator("#cardSeries").check()
        W(600)
        pg.locator("#cardLong").check()
        W(600)
        with pg.expect_file_chooser() as fc:
            pg.click("#cardFontPick")
        fc.value.set_files(str(BAD))
        W(1500)
        toast = ev("$('toast').textContent")
        check("글꼴이 아닌 파일 → 쉬운 말 안내 · 그대로 못 찾음", "글꼴 파일이 아니에요" in toast and not api("/api/style/card_font")["found"], toast)
        with pg.expect_file_chooser() as fc:
            pg.click("#cardFontPick")
        fc.value.set_files(str(FONT))
        pg.wait_for_function("!document.getElementById('cardFontReset').hidden", timeout=20000)
        W(800)
        s1 = ev("({say: $('cardFontSay').textContent, reset: $('cardFontReset').hidden})")
        cf = api("/api/style/card_font")
        copied = [f for f in os.listdir(WORK / "styles" / "설정" / "은평 그림") if f.startswith("font_")]
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s2_font_picked.png"))
        check("'글꼴 파일 고르기'(한글 이름 글꼴 파일) → 작업 폴더에 복사 · 줄에 그 글꼴 이름 · '처음으로' 보임",
              s1["say"] == "글꼴: Do Hyeon (고른 파일 · KFA고딕 테스트.ttf)" and not s1["reset"] and cf["found"] and cf["src"] == "picked" and len(copied) == 1,
              f"{s1['say']} · {copied}")

        # ---- 2. 편집실: 가편집 은평 스타일 쇼츠의 모든 글자 = 그 글꼴 ----
        pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(N1)}")
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
        W(1500)
        q0 = ev("(P.sequences.find(q => q.format === 'shorts' && epFontOn(q)) || {}).id")
        ev(f"switchSeq('{q0}')")
        W(1200)
        pg.wait_for_function("document.fonts.check('40px \"EpFontE\"')", timeout=20000)
        ev("seek(1.0)")
        W(2500)
        ev("selX = null; if (window.selI) selI.clear(); renderTimeline(); frameDirty = true")
        W(1000)
        dom = ev("""(() => ({ epf: EPF && EPF.family, on: epFontOn(S), loaded: document.fonts.check('40px "EpFontE"'),
              txt: [...document.querySelectorAll('#ov .txt')].filter(e => e.offsetParent && e.textContent.trim()).map(e => ({ t: e.textContent, f: e.style.fontFamily, w: e.style.fontWeight })),
              hook: (S.titles.find(t => !t.plan && t.start < 0.05) || S.titles[0]), titles: S.titles.map(t => ({ text: t.text, plan: t.plan || "", font: t.style.font || "" })) }))()""")
        pv = SHOTS / "e1_preview_1s.png"
        pg.locator("#frame").screenshot(path=str(pv))
        kinds = {t["plan"] for t in dom["titles"]}
        check("편집실: 서버가 준 글꼴(Do Hyeon)을 받아 불러옴 · 은평 스타일 편집본",
              dom["epf"] == "Do Hyeon" and dom["on"] and dom["loaded"], json.dumps({k: dom[k] for k in ("epf", "on", "loaded")}))
        check("미리보기의 모든 글자(큰 제목·(사실 줄)·이름표·강좌 줄·말 자막) = 그 글꼴 · 굵기 흉내 없음",
              len(dom["txt"]) >= 4 and all(x["f"].replace('"', "").startswith("EpFontE") and x["w"] == "400" for x in dom["txt"]) and {"series", "longline"} <= kinds
              and any(x["t"] == dom["hook"]["text"] for x in dom["txt"]),
              json.dumps(dom["txt"], ensure_ascii=False)[:500])
        # 서버 셈(저장소 editor.ep_title_style · 같은 글꼴)과 같은 큰 제목 크기
        import epfont  # noqa: E402
        import editor  # noqa: E402
        import style  # noqa: E402
        style.STYLES = WORK / "styles"
        epfont.FONT_DIRS = []
        epfont.forget()
        hook = dom["hook"]
        want = editor.ep_title_style(hook["text"])
        check("큰 제목 크기 = 서버 셈(그 글꼴 폭으로 흰 상자에 맞춤)", hook["style"]["size"] == want["size"] and hook["style"]["y"] == want["y"],
              f"{hook['text']} · 미리보기 {hook['style']['size']} · 서버 {want['size']}")

        # ---- 3. 미리보기 = 내보낸 장면 (1080×1920 · 흰 상자 안 큰 제목 글씨 자리 · 글자 폭 = 그 글꼴로 잰 폭) ----
        proj = ev("exportProject()")
        r = api("/api/edit/frame", {"name": N1, "project": proj, "t": 1.0})
        fr = WORK / "out" / r["file"]
        shutil.copyfile(fr, SHOTS / "e1_export_1s.png")
        a = np.asarray(Image.open(fr).convert("RGB")).astype(int)
        Hh, Ww = a.shape[:2]
        pvi = np.asarray(Image.open(pv).convert("RGB").resize((Ww, Hh), Image.LANCZOS)).astype(int)
        bx = editor.EP_BOX
        y0, y1, x0, x1 = int(bx["y"] * Hh) + 4, int((bx["y"] + bx["h"]) * Hh) - 4, int(bx["x"] * Ww) + 4, int((bx["x"] + bx["w"]) * Ww) - 4

        def ink(img):
            m = img[y0:y1, x0:x1].max(axis=2) < 110
            ys, xs = np.nonzero(m.sum(axis=1) >= 2)[0], np.nonzero(m.sum(axis=0) >= 2)[0]
            return (int(xs.min()) + x0, int(ys.min()) + y0, int(xs.max()) + x0, int(ys.max()) + y0) if len(xs) else None
        ie, ip = ink(a), ink(pvi)
        real = ImageFont.truetype(str(FONT), hook["style"]["size"]).getlength(hook["text"])
        ok = ie and ip and all(abs(u - v) <= 8 for u, v in zip(ie, ip)) and abs((ie[2] - ie[0]) - real) <= real * 0.04
        check("흰 상자 큰 제목: 내보낸 장면 = 미리보기 (글씨 자리 8화소 안) · 글씨 폭 = 그 글꼴로 잰 폭 (4% 안)", ok,
              f"내보내기 {ie} · 미리보기 {ip} · 그 글꼴 폭 {real:.0f}")
        Image.fromarray(np.concatenate([pvi, a], axis=1).astype(np.uint8)).resize((Ww, Hh // 2)).save(SHOTS / "e1_preview_vs_export.png")

        # ---- 4. 다른 템플릿 → 예전 글꼴 · 다시 은평 스타일 → 그 글꼴 ----
        ev("commit('템플릿'); TEMPLATES['위아래 검정 박스'](); changed(false)")
        W(1200)
        f1 = ev("[...document.querySelectorAll('#ov .txt')].filter(e => e.offsetParent && e.textContent.trim()).map(e => e.style.fontFamily)")
        ev("commit('템플릿'); TEMPLATES['은평 스타일'](); changed(false)")
        W(1200)
        f2 = ev("[...document.querySelectorAll('#ov .txt')].filter(e => e.offsetParent && e.textContent.trim()).map(e => e.style.fontFamily)")
        pg.locator("#frame").screenshot(path=str(SHOTS / "e2_template_again.png"))
        check("다른 템플릿 → 예전 글꼴 (EpFontE 없음) · 다시 '은평 스타일' → 그 글꼴",
              f1 and not any("EpFontE" in f for f in f1) and f2 and all(f.replace('"', "").startswith("EpFontE") for f in f2), f"{f1} {f2}")
        tsel = ev("(() => { const t = S.titles.find(x => !x.plan); if (!t) return null; selX = { k: 'title', id: t.id }; renderPanels(); "
                  "const s = document.querySelector('select[id$=\"font\"]'); return s ? { dis: s.disabled, tip: s.title } : null; })()")
        check("타이틀 글꼴 고르기 칸은 막히고 'KFA 글꼴로 고정' 안내", tsel and tsel["dis"] and "KFA 글꼴(Do Hyeon)로 고정" in tsel["tip"], json.dumps(tsel, ensure_ascii=False))
        ev("doSave && doSave()")
        W(1500)

        # ---- 5. 스타일 화면 '처음으로' → 못 찾음 ----
        pg.goto(f"http://127.0.0.1:{PORT}/")
        W(1500)
        pg.locator('[data-p="style"]').first.click()
        W(1200)
        ev("document.getElementById('cardFont').scrollIntoView({block: 'center'})")
        pg.click("#cardFontReset")
        W(1200)
        s2 = ev("({say: $('cardFontSay').textContent, reset: $('cardFontReset').hidden, toast: $('toast').textContent})")
        pg.locator("#autoSet").screenshot(path=str(SHOTS / "s3_font_reset.png"))
        check("'처음으로' → 고른 글꼴을 빼고 못 찾음 한 줄 · 복사한 파일은 남음",
              s2["say"].startswith("KFA 글꼴을 이 PC에서 찾지 못했어요") and s2["reset"] and not api("/api/style/card_font")["found"]
              and any(f.startswith("font_") for f in os.listdir(WORK / "styles" / "설정" / "은평 그림")), json.dumps(s2, ensure_ascii=False))
        check("JS 오류 없음", not errs, "; ".join(errs[:3]))
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()

n_ok = sum(1 for _, ok in res if ok)
print(f"{n_ok}/{len(res)} 통과")
sys.exit(0 if n_ok == len(res) else 1)
