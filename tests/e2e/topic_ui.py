"""쇼츠 첫머리 주제 줄 화면 e2e (BR-120 · D-220·D-221 · 개발용 · 배포 안 됨 · unittest 가 줍지 않음) — 마지막 줄 'N/N 통과' 면 통과.

실행: TOPIC_SRC=<시험 작업 폴더> [TOPIC_REPO=<저장소>] [TOPIC_PORT=9482] [TOPIC_OUT=<출력 폴더>] python3 tests/e2e/topic_ui.py
- TOPIC_SRC: videos/ 와 analysis/<영상 이름>/ 이 있는 작업 폴더 — MSG 시험 원본 MSGRAW01(퍼스트 터치 레슨)·MSGRAW02(패스 앤 무브 드릴)
  (영상은 저장소에 넣지 않음 · 리드 개발 환경은 `$SCRATCH/topic_ev2/work` · analysis 에 msg_signals.json 이 있으면 MSG 후보가 빨리 나옴)
- 저장소(기본: 이 파일의 저장소)를 TOPIC_OUT/app 으로 복사(config.json 은 시험용)해 띄우고, 작업 폴더 TOPIC_OUT/work 는 매번 새로 · 스크린샷 TOPIC_OUT/shots
확인: 스타일 화면 설정 켜기/끄기(지금 값 GET) · 가편집 쇼츠 주제 줄 자리·2.5초 · 글 고치기·Delete·Ctrl+Z · 앞을 잘라도(Q·대사 ✂·군더더기 정리) 맨 앞 그대로 ·
레이아웃 틀(가편집 · MSG 쇼츠: 끝의 '다시 보기 ▶' 말고 자기 큰 줄에 맞춤) · 두 줄 큰 제목 쇼츠의 바꿔 끼우기(미리보기 · 지우면 큰 제목이 처음부터) ·
'새 쇼츠 편집본으로 추가' = 서버 가편집 · 편집실을 연 채 설정을 끄면 추천 카드·새 쇼츠에 안 넣음 · 저장/다시 열기 · 미리보기 = 내보낸 장면 · JS 오류 0."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(os.environ.get("TOPIC_REPO") or Path(__file__).resolve().parents[2])
SRC = Path(os.environ.get("TOPIC_SRC", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/topic_ev2/work"))
OUT = Path(os.environ.get("TOPIC_OUT", "/tmp/claude-0/-home-user-wavely/0fb55fd4-356e-5366-99c4-2fddb0b4f464/scratchpad/topic_e2e"))
PORT = int(os.environ.get("TOPIC_PORT", "9482"))
CHROME = os.environ.get("TOPIC_CHROME", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
APP, WORK, SHOTS = OUT / "app", OUT / "work", OUT / "shots"
N1 = "20261008_MSGRAW01_퍼스트 터치 레슨.mp4"
N2 = "20261008_MSGRAW02_패스 앤 무브 드릴.mp4"
res = []


def check(name, ok, info=""):
    res.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  · {info}" if info else ""), flush=True)


def api(path, body=None, timeout=300):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wait_job(limit=900):
    for _ in range(limit * 2):
        st = api("/api/state?since=999999")
        if not st.get("job"):
            return st
        time.sleep(0.5)
    return api("/api/state?since=999999")


# ---- 시험 앱 · 작업 폴더 ----
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
for n in (N1, N2):
    src = (SRC / "videos" / n).resolve()
    try:
        os.link(src, WORK / "videos" / n)
    except OSError:
        os.symlink(src, WORK / "videos" / n)
    shutil.copytree(SRC / "analysis" / n[:-4], WORK / "analysis" / n[:-4], ignore=shutil.ignore_patterns("frames"))
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

BOXES = """(() => { const F = document.getElementById('frame').getBoundingClientRect();
  return [...document.querySelectorAll('#ov .txt')].map(el => { const r = el.getBoundingClientRect();
    return {t: el.textContent, top: (r.top - F.top) / F.height, bottom: (r.bottom - F.top) / F.height, left: (r.left - F.left) / F.width, right: (r.right - F.left) / F.width}; }); })()"""
TL = ("(q => q.titles.map(t => ({id: t.id, text: t.text, plan: t.plan || null, swap: !!t.swap, under: t.under || null, y: t.style.y, x: t.style.x ?? 0.5, "
      "align: t.style.align || 'center', size: t.style.size, start: Math.round(t.start * 1000) / 1000, dur: Math.round(t.dur * 1000) / 1000})))")
TITLES = TL + "(S)"

try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("dialog", lambda d: d.accept())
        ev, W = pg.evaluate, pg.wait_for_timeout

        def boxes():
            return ev(BOXES)

        def open_editor(name):
            pg.goto(f"http://127.0.0.1:{PORT}/editor?name={urllib.request.quote(name)}")
            pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=300000)
            W(1500)

        def at(t):
            ev(f"seek({t})")
            W(800)

        def undo():
            pg.mouse.click(700, 15)
            pg.keyboard.press("Control+z")
            W(500)

        # ---- 1. 스타일 화면 설정 · 지금 값 GET ----
        pg.goto(f"http://127.0.0.1:{PORT}/")
        W(1500)
        ev("loadStyles()")
        W(800)
        on0 = ev("document.getElementById('shortsTopic') && document.getElementById('shortsTopic').checked")
        lbl = ev("document.getElementById('autoSet') ? document.getElementById('autoSet').innerText : ''")
        pg.locator('[data-p="style"]').first.click()
        W(500)
        ev("document.getElementById('autoSet').scrollIntoView()")
        pg.screenshot(path=str(SHOTS / "u1_style_setting.png"))
        pg.locator("#shortsTopic").click()
        W(800)
        off, off_now = api("/api/style/list")["shortsTopic"], api("/api/style/shorts_topic")["on"]
        pg.locator("#shortsTopic").click()
        W(800)
        on1, on_now = api("/api/style/list")["shortsTopic"], api("/api/style/shorts_topic")["on"]
        check("스타일 화면: '쇼츠 첫머리에 주제 한 줄 넣기' 기본 켬 · 끄기/켜기가 저장되고 지금 값(GET)도 같음",
              on0 and off is False and off_now is False and on1 is True and on_now is True and "주제 한 줄" in lbl, f"{on0} {off} {off_now} {on1} {on_now}")

        # ---- 2. 편집실: 가편집 쇼츠의 주제 줄 ----
        open_editor(N1)
        sq = ev(f"P.sequences.filter(q => q.format === 'shorts').map(q => ({{id: q.id, name: q.name, titles: {TL}(q)}}))")
        withk = [q for q in sq if any(t["plan"] == "topic" for t in q["titles"])]
        topic_of = lambda q: next(t for t in q["titles"] if t["plan"] == "topic")  # noqa: E731
        check("가편집 쇼츠에 주제 줄 타이틀 (plan topic · 0~2.5초 · 맞춘 큰 제목 under)",
              withk and all(topic_of(q)["start"] == 0 and topic_of(q)["dur"] <= 2.5 and topic_of(q)["under"] for q in withk),
              json.dumps([[t["text"] for t in q["titles"]] for q in sq], ensure_ascii=False))
        q0 = withk[0]
        k0 = topic_of(q0)
        kid, ktext = k0["id"], k0["text"]
        top = next(t for t in q0["titles"] if t["id"] == k0["under"])
        ev(f"switchSeq('{q0['id']}')")
        W(800)
        at(1.0)
        bx = boxes()
        kb = next((x for x in bx if x["t"] == ktext), None)
        tb = next((x for x in bx if x["t"] == top["text"]), None)
        pg.screenshot(path=str(SHOTS / "u2_preview_topic.png"))
        check("미리보기: 주제 줄이 위 큰 제목 바로 위 · 안 겹침 · 위 7% 아래 · 좌우 안", kb and tb and kb["bottom"] <= tb["top"] + 0.002 and kb["top"] >= 0.065
              and kb["left"] >= 0.05 and kb["right"] <= 0.95, json.dumps([kb, tb], ensure_ascii=False))
        at(3.0)
        check("2.5초 뒤에는 주제 줄이 사라지고 위 큰 제목만", ktext not in [x["t"] for x in boxes()] and any(x["t"] == top["text"] for x in boxes()))

        # ---- 3. 앞을 잘라도 맨 앞 그대로 (검토: Q·대사 ✂·군더더기 정리에서 사라지거나 짧아짐) ----
        def head():
            return ev(f"(() => {{ const k = S.titles.find(t => t.id === '{kid}'); return k ? [Math.round(k.start * 1000) / 1000, Math.round(k.dur * 1000) / 1000] : null; }})()")
        at(3.0)
        pg.mouse.click(700, 15)
        pg.keyboard.press("q")
        W(700)
        after_q = head()
        undo()
        back_q = head()
        cut = ev("""(() => { const v = S.items.filter(i => i.track === 'V1' && i.media === 'main').sort((a, b) => a.start - b.start)[0];
          const c = P.captions.filter(c => c.end > v.in + 0.05 && c.start < v.in + 2.5).sort((a, b) => a.start - b.start)[0]; if (!c) return null;
          const el = document.querySelector(`[data-cx="${c.id}"]`); if (el) el.click(); else extractSource(c.start, c.end); return [c.start, c.end, !!el]; })()""")
        W(700)
        after_x = head()
        undo()
        pg.click('[data-lt="rec"]')
        W(800)
        ev("(() => { const d = document.getElementById('recMore'); if (d && !d.open) d.open = true; })()")
        W(300)
        tl0 = ev("total()")
        pg.locator("#recFull").click()       # 원본 전체로 되돌리기 (앞에 잘라 낸 원본이 다시 들어옴) → 군더더기 정리
        W(900)
        tl1, after_f = ev("total()"), head()
        pg.locator("#recTidy").click()
        W(900)
        tl2, after_t = ev("total()"), head()
        undo()
        undo()
        check("앞을 잘라도 주제 줄은 맨 앞 0~2.5초 그대로 (Q 앞 잘라내기 · 첫 대사 ✂ · 원본 전체로 · 군더더기 정리) · Ctrl+Z",
              after_q == [0, 2.5] and back_q == [0, 2.5] and cut and after_x == [0, 2.5] and after_f == [0, 2.5] and after_t == [0, 2.5]
              and tl1 > tl0 > 0 and tl2 < tl1 and head() == [0, 2.5],
              f"Q {after_q} → {back_q} · ✂ {cut} {after_x} · 원본 전체로 {tl0:.1f}→{tl1:.1f}초 {after_f} · 정리 →{tl2:.1f}초 {after_t}")

        # ---- 4. 글 고치기 · 지우기 · 되돌리기 ----
        at(1.0)
        ev(f"selX = {{k: 'title', id: '{kid}'}}; selI.clear(); setRtab('fx', true); renderTimeline(); renderPanels()")
        W(500)
        pg.locator("#pTitleText").fill("퍼스트 터치 레슨 · 직접 고침")
        pg.locator("#pTitleText").dispatch_event("input")
        W(600)
        txt = ev(f"S.titles.find(t => t.id === '{kid}').text")
        ov = [x["t"] for x in boxes()]
        check("주제 줄 글 고치기 → 미리보기에 바로", txt == "퍼스트 터치 레슨 · 직접 고침" and "퍼스트 터치 레슨 · 직접 고침" in ov, f"{txt} {ov}")
        pg.mouse.click(700, 15)
        ev(f"selX = {{k: 'title', id: '{kid}'}}; selI.clear(); renderTimeline()")
        pg.keyboard.press("Delete")
        W(400)
        gone = ev(f"!S.titles.some(t => t.id === '{kid}') && S.titles.some(t => t.id === '{top['id']}' && t.start === 0)")
        pg.keyboard.press("Control+z")
        W(500)
        back = ev(f"S.titles.some(t => t.id === '{kid}' && t.text === '퍼스트 터치 레슨 · 직접 고침')")
        check("주제 줄 지우기(Delete) → 위 큰 제목은 남음 · Ctrl+Z 로 되돌림", gone and back, f"{gone} {back}")

        # ---- 5. 레이아웃 틀 (가편집) ----
        lay = {}
        for tpl in ("꽉 채우기", "위아래 흰 박스", "위아래 검정 박스", "블러 배경"):
            ev(f"commit('템플릿'); TEMPLATES['{tpl}'](); changed(false)")
            W(400)
            lay[tpl] = ev(f"""(() => {{ const k = S.titles.find(t => t.id === '{kid}'), top = S.titles.find(t => t.id === k.under);
                const a = txtBox(k.text, k.style, 1080, 1920), b = txtBox(top.text, top.style, 1080, 1920), c = capBand(S.captionStyle);
                return {{k: a, t: b, c, swap: !!k.swap, fill: k.style.fill, tfill: top.style.fill, ts: top.start}}; }})()""")
        ok = all((v["k"][3] <= v["t"][1] + 1e-6 or v["k"][1] >= v["t"][3] - 1e-6 or (v["swap"] and v["ts"] >= 2.5)) and v["k"][1] >= 0.07 - 1e-6
                 and not (v["c"][0] < v["k"][3] + 0.012 and v["k"][1] < v["c"][1] + 0.012) for v in lay.values())
        check("레이아웃 틀(꽉 채우기·흰 박스·검정 박스·블러): 주제 줄을 큰 제목에 맞춰 다시 놓음 (안 겹침 · 말 자막 띠 밖 · 위 7% 아래)", ok,
              json.dumps(lay, ensure_ascii=False)[:500])
        pg.screenshot(path=str(SHOTS / "u3_template.png"))

        # ---- 6. 추천 탭 '새 쇼츠 편집본으로 추가' = 서버 가편집과 같은 자리 ----
        pg.click('[data-lt="rec"]')
        W(600)
        ev("(() => { const d = document.getElementById('recMore'); if (d && !d.open) d.open = true; })()")
        W(300)
        idx = ev("REC.shorts.findIndex(s => s.topic)")
        want = ev(f"REC.shorts[{idx}].topic")
        card = pg.inner_text("#lt-rec")
        n0 = ev("P.sequences.length")
        pg.locator(f'[data-rec="{idx}"]').click()
        W(1000)
        newq = ev(f"({{n: P.sequences.length, titles: {TITLES}}})")
        srvk = next((t for q in sq for t in q["titles"] if t["plan"] == "topic" and t["text"] == want), None)
        nk = next((t for t in newq["titles"] if t["plan"] == "topic"), None)
        check("'새 쇼츠 편집본으로 추가': 주제 줄이 서버 가편집과 같은 글·자리·크기 · 추천 카드에 주제 줄 표시",
              newq["n"] == n0 + 1 and nk and srvk and (nk["text"], nk["y"], nk["size"], nk["dur"], nk["swap"]) == (srvk["text"], srvk["y"], srvk["size"], srvk["dur"], srvk["swap"])
              and f"첫머리 주제 줄 · {want}" in card, json.dumps([nk, srvk], ensure_ascii=False))

        # ---- 7. 편집실을 연 채 설정을 끄면 추천 카드·새 쇼츠에 안 넣음 (검토) ----
        api("/api/style/shorts_topic", {"on": False})
        ev("renderRec()")
        W(800)
        card_off = pg.inner_text("#lt-rec")
        n1 = ev("P.sequences.length")
        pg.locator(f'[data-rec="{idx}"]').click()
        W(1000)
        off_q = ev(f"({{n: P.sequences.length, titles: {TITLES}}})")
        api("/api/style/shorts_topic", {"on": True})
        ev("renderRec()")
        W(800)
        card_on = pg.inner_text("#lt-rec")
        check("편집실을 연 채 스타일 화면에서 끄면: 추천 카드 표시 없음 · '새 쇼츠 편집본으로 추가'에 주제 줄 없음 · 다시 켜면 표시",
              "첫머리 주제 줄" not in card_off and off_q["n"] == n1 + 1 and not [t for t in off_q["titles"] if t["plan"] == "topic"] and f"첫머리 주제 줄 · {want}" in card_on,
              json.dumps(off_q["titles"], ensure_ascii=False)[:200])
        ev("saveNow()")
        W(3000)

        # ---- 8. 저장 → 다시 열기 ----
        pg.reload()
        pg.wait_for_function("document.getElementById('saved') && document.getElementById('saved').textContent.includes('자동 저장')", timeout=120000)
        W(1200)
        kept = ev(f"P.sequences.some(q => q.titles.some(t => t.id === '{kid}' && t.text === '퍼스트 터치 레슨 · 직접 고침' && t.plan === 'topic' && t.under))")
        check("저장 → 다시 열기: 고친 주제 줄(글·plan·under) 그대로", kept)

        # ---- 9. MSG 쇼츠 + 레이아웃 틀: 자기 큰 줄에 맞춤 (검토: 끝의 '다시 보기 ▶'에 맞춰 큰 줄 위에 오른쪽 정렬로 겹침) ----
        r = api("/api/edit/msg", {"name": N1, "styles": [{"kind": "preset", "name": "담백 레슨형"}], "intensity": "보통", "kinds": ["shorts"], "first": 0})
        st = wait_job() if r.get("ok") else {}
        if st.get("resultBig"):
            st = api("/api/state?since=999999&result=1")
        mres = (st or {}).get("result") or {}
        ok_msg = bool(mres.get("sequences"))
        msg_rows = {}
        if ok_msg:
            ev("(res => msgAdd(res))", mres)
            W(1500)
            first_titles = ev(f"({TITLES}).filter(t => t.start < 0.05)")
            order = ev("S.titles.map(t => t.text)")
            for tpl in ("위아래 검정 박스", "블러 배경", "꽉 채우기"):
                ev(f"commit('템플릿'); TEMPLATES['{tpl}'](); changed(false)")
                at(1.0)
                kt = ev("(() => { const k = S.titles.find(t => t.plan === 'topic'); return k ? {text: k.text, under: k.under, x: k.style.x ?? 0.5, align: k.style.align || 'center'} : null; })()")
                if not kt:
                    msg_rows[tpl] = None
                    continue
                main = ev(f"(() => {{ const m = S.titles.find(t => t.id === '{kt['under']}'); return {{text: m.text, x: m.style.x ?? 0.5, align: m.style.align || 'center'}}; }})()")
                bx = {x["t"]: x for x in boxes()}
                kb, mb = bx.get(kt["text"]), bx.get(main["text"])
                msg_rows[tpl] = {"k": kb, "m": mb, "kx": [kt["x"], kt["align"]], "mx": [main["x"], main["align"]]}
                pg.screenshot(path=str(SHOTS / f"u5_msg_{tpl.replace(' ', '_')}.png"))
            ok_msg = all(v and v["k"] and v["m"] and (v["k"]["bottom"] <= v["m"]["top"] + 0.003 or v["k"]["top"] >= v["m"]["bottom"] - 0.003)
                         and v["kx"] == v["mx"] for v in msg_rows.values())
        check("MSG 쇼츠(담백 레슨형 · 보통): 레이아웃 틀을 바꿔도 주제 줄은 자기 큰 줄 위·같은 가운데 정렬 · 안 겹침 (titles 맨 앞이 다른 글이어도)",
              ok_msg, json.dumps({"first": ok_msg and first_titles, "order0": ok_msg and order[:2], "rows": msg_rows}, ensure_ascii=False)[:600])

        # ---- 10. 미리보기 = 내보낸 장면 (처음 3초 구간 내보내기 · 가편집 쇼츠) ----
        q2 = next(q for q in withk if q["id"] != q0["id"]) if len(withk) > 1 else q0
        ev(f"switchSeq('{q2['id']}')")
        W(800)
        at(1.0)
        k2 = topic_of(q2)
        pb = {x["t"]: x for x in boxes()}
        proj = ev("({...JSON.parse(JSON.stringify(S)), captions: P.captions, info: P.info, source: NAME, media: P.media})")
        before = set(os.listdir(WORK / "out")) if (WORK / "out").exists() else set()
        api("/api/edit/export", {"name": N1, "project": proj, "opts": {"preset": "youtube", "range": [0, 3], "video": True, "srt": False, "xml": False, "hw": False}})
        wait_job(600)
        mp4 = sorted([f for f in os.listdir(WORK / "out") if f.endswith(".mp4") and f not in before])
        ff = os.popen("python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())'").read().strip() or "ffmpeg"
        fr = SHOTS / "u4_export_1s.png"
        subprocess.run([ff, "-v", "error", "-y", "-i", str(WORK / "out" / mp4[-1]), "-ss", "1.0", "-frames:v", "1", str(fr)], check=True)
        pg.screenshot(path=str(SHOTS / "u4_preview_1s.png"))
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(fr).convert("RGB")).astype(int)
        H = a.shape[0]
        white = (a[:, :, 0] > 225) & (a[:, :, 1] > 225) & (a[:, :, 2] > 225)
        yellow = (a[:, :, 0] > 200) & (a[:, :, 1] > 180) & (a[:, :, 2] < 120)

        def span(mask, lo, hi):
            rows = [y for y in range(int(lo * H), int(hi * H)) if mask[y].sum() > 6]
            return (rows[0] / H, rows[-1] / H) if rows else None
        ek, et = span(white, 0.03, 0.2), span(yellow, 0.1, 0.3)
        pk, pt = pb.get(k2["text"]), next((v for t, v in pb.items() if t != k2["text"] and v["top"] < 0.3), None)

        def inside(e, p_):
            return e and p_ and p_["top"] - 0.015 <= e[0] and e[1] <= p_["bottom"] + 0.015
        check("미리보기 = 내보낸 장면 (1초 · 주제 줄 흰 글자·큰 제목 노란 글자의 세로 자리가 미리보기 글 상자 안)", inside(ek, pk) and inside(et, pt),
              f"export kicker {ek} title {et} · preview {pk and (round(pk['top'], 3), round(pk['bottom'], 3))} {pt and (round(pt['top'], 3), round(pt['bottom'], 3))}")

        # ---- 11. 두 줄·대사 그대로인 큰 제목 쇼츠 (MSGRAW02): 처음 2.5초 바꿔 끼우기 · 지우면 큰 제목이 처음부터 ----
        open_editor(N2)
        sw = ev(f"P.sequences.filter(q => q.format === 'shorts').map(q => ({{id: q.id, titles: {TL}(q)}}))")
        swq = [q for q in sw if any(t["plan"] == "topic" and t["swap"] for t in q["titles"])]
        ok11 = len(swq) == len(sw) == 2
        info11 = json.dumps([[(t["text"], t["start"], t["dur"], t["swap"]) for t in q["titles"]] for q in sw], ensure_ascii=False)
        if ok11:
            q = swq[0]
            k = next(t for t in q["titles"] if t["plan"] == "topic")
            big = next(t for t in q["titles"] if t["id"] == k["under"])
            ev(f"switchSeq('{q['id']}')")
            W(800)
            at(1.0)
            b1 = {x["t"]: x for x in boxes()}
            pg.screenshot(path=str(SHOTS / "u6_swap_1s.png"))
            at(3.0)
            b3 = {x["t"]: x for x in boxes()}
            ok11 = k["text"] in b1 and big["text"] not in b1 and big["text"] in b3 and k["text"] not in b3 and abs(b1[k["text"]]["bottom"] - b3[big["text"]]["bottom"]) < 0.01
            info11 += f" · 1초 {list(b1)} · 3초 {list(b3)}"
            ev(f"selX = {{k: 'title', id: '{k['id']}'}}; selI.clear(); renderTimeline()")
            pg.mouse.click(700, 15)
            ev(f"selX = {{k: 'title', id: '{k['id']}'}}; selI.clear(); renderTimeline()")
            pg.keyboard.press("Delete")
            W(400)
            at(1.0)
            restored = ev(f"(() => {{ const t = S.titles.find(x => x.id === '{big['id']}'); return [t.start, Math.round(t.dur * 100) / 100]; }})()")
            b1d = [x["t"] for x in boxes()]
            undo()
            again = ev(f"(() => {{ const t = S.titles.find(x => x.id === '{big['id']}'); return [S.titles.some(x => x.id === '{k['id']}'), t.start]; }})()")
            ok11 = ok11 and restored[0] == 0 and abs(restored[1] - round(big["start"] + big["dur"], 2)) < 0.02 and big["text"] in b1d and again == [True, 2.5]
            info11 += f" · 지우면 {restored} {b1d} · 되돌리면 {again}"
        check("두 줄·대사 그대로인 큰 제목 쇼츠: 처음 2.5초는 큰 제목 자리에 주제 줄 → 그 뒤 큰 제목 · 주제 줄을 지우면 큰 제목이 처음부터 · Ctrl+Z", ok11, info11[:600])
        check("화면 오류 없음", not [e for e in errs if "pageerror" in e], str(errs[:3]))
        b.close()
finally:
    srv.terminate()
n = sum(1 for _, ok in res if ok)
print(f"\n{n}/{len(res)} 통과")
sys.exit(0 if n == len(res) else 1)
