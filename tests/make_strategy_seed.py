"""채널 전략 비교 데이터 strategy_seed.json 다시 만들기 (개발용 · tests/ 라서 배포되지 않음 · D-024).

python3 tests/make_strategy_seed.py [--work <실제로 새로 고친 작업 폴더 WORK>] [--research <조사 원자료 폴더 (ch_*.json)>] [--out <출력 파일>]
- 실제로 새로 고친 채널: WORK/strategy/channels/*.json (앱의 새로 고침 결과) → 형식마다 최근 30개만 남겨 그대로 (새로 고친 날 숫자)
- 나머지 추천 채널: --research 를 주면 조사 원자료 ch_*.json (2026-10-07 · 형식 제각각 · 제목 일부 영어 · 날짜 없음)을 변환,
  주지 않으면 지금 저장소의 strategy_seed.json 에 있는 그 채널 기록을 그대로 씀 (조사 원자료가 없어도 새로 고친 채널만 바꿔 다시 만들 수 있게)
- 형식은 channels/<fid>.json 과 같고 src 는 'seed'. 실행 뒤 python3 -m unittest tests.test_strategy 의 test_seed_file_valid 로 확인.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import strategy  # noqa: E402

AT = datetime(2026, 10, 7, 12, tzinfo=timezone.utc).timestamp()
PER = 30

# ref_channels.json 이름 → 조사 원자료 번호 (조사 순서와 추천 목록 순서가 달라 손으로 맞춤)
MAP = {"쌈바 풋살 클래스": 13, "샌드박스 풋살": 20, "명싸커": 16, "풋살의민족": 17, "쪼살": 15, "한국풋살연맹 KFL": 23, "축구도전(축도)": 25,
       "풋살 엄살라": 22, "풋살해주호": 18, "풋살코리아": 24, "아이콘 풋살": 27, "주재파악 TV": 30, "영타": 29, "용인 케이식스": 28,
       "유니팝(KICK OH)": 31, "JK 아트사커": 2, "티아고킴": 39, "축구도사 메기": 34, "축구배울래?": 38, "사커 튜토리얼": 21, "서벤사커": 35,
       "두개의심장": 41, "축정원": 14, "강코치 풋볼": 40, "고알레 아카데미": 32, "풋볼아이": 37, "왼발의 청춘": 26, "지니풋볼": 42,
       "감스트": 8, "슛포러브": 0, "안정환 19": 33, "고알레": 4, "이거해조 원희형": 5, "도블락": 1, "석꾸축꾸": 43, "동네축구 고수 DONGO": 36,
       "이동방송국(이동국)": 7, "김진짜": 45, "달수네라이브": 9, "리춘수(이천수)": 6, "새벽의 축구 전문가": 10, "꽁병지TV": 3, "말년 호빙요": 47,
       "더투탑": 11, "축구친구": 46, "이스타": 19, "축구심동": 44, "이제풋볼 축구화 연구소": 48, "볼만찬 기자들": 51, "축구화 리뷰하는 약사": 49,
       "키킷 KICKiT": 50}


def ents(x):
    if isinstance(x, dict):
        return x.get("entries") or []
    return x if isinstance(x, list) else []


def row(e):
    t = e.get("t") if e.get("t") is not None else e.get("title")
    d = e.get("d") if "d" in e else e.get("dur") if "dur" in e else e.get("duration")
    v = e.get("v") if "v" in e else e.get("views") if "views" in e else e.get("view_count")
    return {"id": e.get("id"), "t": str(t or "")[:300], "d": d if isinstance(d, (int, float)) and d > 0 else None,
            "v": v if isinstance(v, (int, float)) and v > 0 else None}


def research(rec, d):
    v, s = d.get("videos"), d.get("shorts")
    if s is None:
        s = d.get("shorts?view=0&sort=p")
    meta = v if isinstance(v, dict) else d
    subs = d.get("subs") or d.get("subscribers") or (v.get("subs") if isinstance(v, dict) else None) or (s.get("subs") if isinstance(s, dict) else None)
    desc = d.get("desc") or d.get("description") or (meta.get("desc") if isinstance(meta, dict) else None)
    ko = {}
    if isinstance(d.get("ko_titles"), dict):
        ko.update({k: t for k, t in d["ko_titles"].items() if isinstance(t, str)})
    for key in ("videos_60_ko", "videos_all_recent15", "shorts_all_recent15"):
        for e in ents(d.get(key)):
            r = row(e)
            if r["id"] and strategy.is_ko(r["t"]):
                ko[r["id"]] = r["t"]
    if isinstance(d.get("last80_ko_titles"), dict):
        for e in ents(d["last80_ko_titles"].get("videos")):
            r = row(e)
            if r["id"] and strategy.is_ko(r["t"]):
                ko[r["id"]] = r["t"]
    key = strategy._rec_key(rec)
    ch = {"v": 1, "key": key, "name": rec["name"], "handle": (rec.get("handle") or "").lower() or None, "url": rec.get("url"),
          "channelId": rec.get("channelId"), "group": rec["group"], "subs": int(subs) if subs else rec.get("subs"), "subsAt": AT,
          "src": "seed", "descFlags": strategy.desc_flags(desc) if desc else None, "tabs": {}, "videos": {}, "rss": None,
          "translated": False, "errors": [], "at": AT, "origin": "조사 2026-10-07"}
    eng = 0
    for tab, k, lst in (("long", "L", ents(v)), ("shorts", "S", ents(s))):
        ids = []
        for e in lst[:PER]:
            r = row(e)
            if not r["id"] or not re.fullmatch(r"[A-Za-z0-9_-]{11}", r["id"]) or r["id"] in ch["videos"]:
                continue
            o = ko.get(r["id"])
            vid = {"k": k, "t": o or r["t"], "v": r["v"]}
            if o:
                vid["o"] = 1
            if r["d"] and k == "L":
                vid["d"] = r["d"]
            if not strategy.is_ko(vid["t"]):
                eng += 1
            ch["videos"][r["id"]] = vid
            ids.append(r["id"])
        ch["tabs"][tab] = {"ids": ids, "complete": False, "n": len(ids), "at": AT}
    ch["translated"] = eng > 0.4 * max(1, len(ch["videos"]))
    return ch


def trim_live(ch):
    c = json.loads(json.dumps(ch))
    keep = set()
    for t in c["tabs"].values():
        t["ids"] = (t.get("ids") or [])[:PER]
        keep |= set(t["ids"])
    keep |= set((c.get("rss") or {}).get("ids") or [])
    c["videos"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("seen", "vAt", "te")} for k, v in c["videos"].items() if k in keep}
    c["src"] = "seed"
    c["origin"] = "새로 고침 " + time.strftime("%Y-%m-%d", time.localtime(c.get("at") or AT))
    c["errors"] = []
    return c


def main():
    ap = argparse.ArgumentParser(description="strategy_seed.json 다시 만들기")
    ap.add_argument("--work", help="실제로 새로 고친 작업 폴더 (WORK)")
    ap.add_argument("--research", help="조사 원자료 폴더 (ch_*.json)")
    ap.add_argument("--out", default=str(REPO / "strategy_seed.json"))
    args = ap.parse_args()
    out_path = Path(args.out)
    rc = json.loads((REPO / "ref_channels.json").read_text(encoding="utf-8"))
    old = {c["key"]: c for c in (json.loads((REPO / "strategy_seed.json").read_text(encoding="utf-8")).get("channels") or [])} \
        if (REPO / "strategy_seed.json").exists() else {}
    live = {}
    if args.work:
        for f in sorted((Path(args.work) / "strategy" / "channels").glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("key") and d.get("videos") and d.get("listedAt", True):  # 목록을 받지 못한 기록(RSS 만)은 쓰지 않음
                live[d["key"]] = d
    out, n_live, n_res, n_old = [], 0, 0, 0
    if "own" in live:
        out.append(trim_live(live["own"]))
        n_live += 1
    elif "own" in old:
        out.append(old["own"])
        n_old += 1
    for rec in rc["channels"]:
        key = strategy._rec_key(rec)
        if not key:
            continue
        lv = live.get(key) or next((c for c in live.values() if rec.get("channelId") and c.get("channelId") == rec["channelId"]), None)
        if lv:
            c = trim_live(lv)
            c.update(key=key, group=rec["group"], name=lv.get("name") or rec["name"])
            out.append(c)
            n_live += 1
            continue
        i = MAP.get(rec["name"])
        if args.research and i is not None and (Path(args.research) / f"ch_{i}.json").exists():
            d = json.loads((Path(args.research) / f"ch_{i}.json").read_text(encoding="utf-8"))
            out.append(research(rec, d))
            n_res += 1
        elif key in old:
            out.append(old[key])
            n_old += 1
        else:
            print("자료 없음:", rec["name"])
    data = {"v": 1, "at": "2026-10-07", "note": "채널 전략 비교 데이터: 추천 채널 51곳 + 우리 채널. 실제로 새로 고친 채널은 그날 숫자, 나머지는 2026-10-07 조사 숫자예요 (반올림된 값 · 날짜 없음).",
            "channels": out}
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"seed {out_path} · 채널 {len(out)} (새로 고친 것 {n_live} · 조사 원자료 {n_res} · 예전 비교 데이터 그대로 {n_old}) · {out_path.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
