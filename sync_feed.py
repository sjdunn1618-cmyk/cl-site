#!/usr/bin/env python3
"""CL Site sync: turns Google Sheets get_values results (saved as JSON files) into
data.json for the CL Site (static site on GitHub Pages).

Usage: python3 sync_feed.py <in_dir> <path/to/data.json>
in_dir holds the raw get_values JSON results:
  saves.json      'Ranking Feed'!O2:S40
  contract.json   'Ranking Feed'!AF2:BA24
  renew.json      'Renewal Rankings Feed'!F1:S70
  tier.json       'Tier Feed'!A1:F30
  banner.json     Banner!A1:B5
  points.json     Tiers!A1:H60
  attendance.json 'Attendance Feed'!G1:K4
  highlights.json Highlights!A1:B60
  pins.json       PINs!A1:B60
  tab_<Tab>.json  '<Tab>'!A1:Z200 for each rep tab (e.g. tab_Joe.json)
  history.json    History!A1:I400 (finished weeks, typed each Monday; optional)
Writes data.json (board numbers in the clear; each rep's minimums and attendance sealed
with AES-GCM under a key derived from that rep's PIN, plus a copy under the manager PIN)
and points_log_rows.json next to in_dir (never inside the site folder): one row per rep
for every History week that is complete (all reps have a Tier), for the weekly Points Log.
Exits with an error and writes nothing if the main feeds are empty or show #REF!.
PINs themselves are never written anywhere.
"""
import json, sys, os, glob, hashlib, datetime, re, base64
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Rep tab name -> full name as it appears on the leaderboards
AGENTS = {
    "Carter": "Carter Bishop", "Colby": "Colby Earl", "Gabe": "Gabe Layton",
    "Jonah": "Jonah Layton", "Joe": "Joseph Black", "Luke": "Luke Thayn",
    "Marcus": "Marcus Neubert", "Mitch": "Mitch McGuire", "Spencer": "Spencer Bishop",
    "Teagan": "Teagan McCune", "Wyatt": "Wyatt Brownell",
}
SALT = "cl-site"

def load(p):
    if not os.path.exists(p): return []
    d = json.load(open(p))
    return d.get("values", []) if isinstance(d, dict) else d

def cell(r, i):
    return (r[i] if i < len(r) else "").strip()

def num(s):
    s = (s or "").replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "-"): return None
    try: return float(s)
    except ValueError: return None

ITER = 150000  # must match ITER in index.html

def pin_key(key, pin):
    return PBKDF2HMAC(hashes.SHA256(), 32, f"{SALT}|{key}".encode(), ITER).derive(pin.encode())

def seal(k, obj):
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), None)
    return {"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}

def save_rows(rows, c0):
    out, total = [], None
    for r in rows:
        name = cell(r, c0)
        if not name or name == "Name": continue
        rec = {"name": name, "rank": num(cell(r, c0+1)), "pct": num(cell(r, c0+2)),
               "saves": num(cell(r, c0+3)), "cancels": num(cell(r, c0+4))}
        if name.lower() == "total":
            total = rec; break
        out.append(rec)
    return out, total

ROSTER = {n.lower() for n in AGENTS.values()}

def on_roster(rows):
    return [r for r in rows if r["name"].lower() in ROSTER]

def rerank(rows):
    """Keep the sheet's order (it ranks on exact values) and renumber 1..n for the team.
    Reps the sheet ranked equal keep sharing a rank."""
    rows = sorted(rows, key=lambda r: (r.get("rank") is None, r.get("rank") or 0))
    out = []
    for i, r in enumerate(rows):
        r = dict(r, _orig=r.get("rank"))
        r["rank"] = out[i-1]["rank"] if i and r["_orig"] == out[i-1]["_orig"] else i + 1
        out.append(r)
    for r in out: r.pop("_orig")
    return out

def save_total(rows):
    sv = sum(r["saves"] or 0 for r in rows); cx = sum(r["cancels"] or 0 for r in rows)
    return {"name": "Total", "rank": None, "pct": round(sv / (sv + cx) * 100, 1) if sv + cx else None,
            "saves": sv, "cancels": cx}

def ren_total(rows):
    t = {"name": "Total", "rank": None}
    for k in ("total", "ic", "ooc", "nc", "count", "unsigned"):
        t[k] = sum(r.get(k) or 0 for r in rows)
    t["acv"] = round(t["total"] / t["count"], 2) if t["count"] else None
    t["signedPct"] = round(t["count"] / (t["count"] + t["unsigned"]) * 100, 2) if t["count"] + t["unsigned"] else None
    return t

def main(src, site_path):
    docs = {"board": {}, "agents": {}}
    def put(coll, doc, data):
        docs[coll][doc] = data

    # --- Safety: never publish a blank site when the IMPORTRANGE feeds are broken ---
    broken = [f for f in ("saves.json", "renew.json", "tier.json")
              if not any(cell(r, 0) and not cell(r, 0).startswith("#") for r in load(os.path.join(src, f)))]
    if broken:
        sys.exit(f"feed empty or #REF! in {', '.join(broken)}: data.json not written")

    # --- True Save % ---
    sv = load(os.path.join(src, "saves.json"))
    srange = cell(sv[0], 0) if sv else ""
    # first block only: stop at the first "Total" row
    overall, ototal = save_rows(sv[1:], 0)
    ct = load(os.path.join(src, "contract.json"))
    ic, ictotal = save_rows(ct[1:], 0)
    oc, octotal = save_rows(ct[1:], 17)
    # only the current team (AGENTS); ranks and team totals recomputed for that group
    overall, ic, oc = (rerank(on_roster(x)) for x in (overall, ic, oc))
    put("board", "saves", {"range": srange.replace("Ranking: ", ""),
        "overall": overall, "overallTotal": save_total(overall),
        "inContract": ic, "inContractTotal": save_total(ic),
        "outContract": oc, "outContractTotal": save_total(oc)})

    # --- Renewal Rankings ---
    rv = load(os.path.join(src, "renew.json"))
    def ren_block(start):
        rows, total, i = [], None, start
        while i < len(rv):
            r = rv[i]; name = cell(r, 0)
            i += 1
            if not name or name == "Name": continue
            rec = {"name": name, "rank": num(cell(r, 1)), "total": num(cell(r, 2)),
                   "ic": num(cell(r, 3)), "ooc": num(cell(r, 4)), "nc": num(cell(r, 5)),
                   "count": num(cell(r, 6)), "acv": num(cell(r, 7)),
                   "unsigned": num(cell(r, 11)), "signedPct": num(cell(r, 12))}
            if name.upper().startswith("TOTAL"):
                rec["name"] = "Total"; total = rec; break
            rows.append(rec)
        return rows, total, i
    rrange, cur_start, ytd_start = "", None, None
    for i, r in enumerate(rv):
        c = cell(r, 0)
        if c.startswith("Ranking:") and cur_start is None:
            rrange = c.replace("Ranking: ", ""); cur_start = i + 1
        if c.upper().startswith("ALL TIME") and ytd_start is None:
            ytd_start = i + 1
    cur, curT, _ = ren_block(cur_start) if cur_start is not None else ([], None, 0)
    ytd, ytdT, _ = ren_block(ytd_start) if ytd_start is not None else ([], None, 0)
    cur, ytd = rerank(on_roster(cur)), rerank(on_roster(ytd))
    put("board", "renewals", {"range": rrange, "current": cur, "currentTotal": ren_total(cur),
        "ytd": ytd, "ytdTotal": ren_total(ytd)})

    # --- Tier Rankings ---
    tv = load(os.path.join(src, "tier.json"))
    trange = cell(tv[0], 0).replace("Ranking: ", "") if tv else ""
    tiers, breakdown, mode = [], {}, None
    for r in tv[1:]:
        name = cell(r, 0)
        if name == "Name": mode = "main"; continue
        if name == "Points Breakdown": mode = "pts"; continue
        if not name: continue
        if mode == "main":
            tiers.append({"name": name, "rph": num(cell(r, 1)), "savePct": num(cell(r, 2)),
                          "total": num(cell(r, 3)), "tier": cell(r, 4), "reward": num(cell(r, 5))})
        elif mode == "pts":
            breakdown[name] = (num(cell(r, 1)), num(cell(r, 2)))
    for i, t in enumerate(tiers):
        t["place"] = i + 1
        t["revPts"], t["savePts"] = breakdown.get(t["name"], (None, None))
    put("board", "tiers", {"range": trange, "rows": tiers})

    # --- Banner ---
    bv = load(os.path.join(src, "banner.json"))
    is_log = lambda r: cell(r, 0).lower().startswith("login log")
    brow = next((r for r in bv[1:] if cell(r, 0) and not is_log(r)), [])
    put("board", "banner", {"label": cell(brow, 0) or "Pending Cancel Count", "value": cell(brow, 1)})
    # Optional: Apps Script web app URL that records PIN logins (Banner row labelled "Login Log URL")
    log_url = next((cell(r, 1) for r in bv[1:] if is_log(r)), "")
    log_url = log_url if re.match(r"^https://script\.google\.com/macros/s/[\w-]+/exec$", log_url) else ""

    # --- Weekly Highlights (Highlights tab: B1 headline, A4 and down one paragraph per row) ---
    hv = load(os.path.join(src, "highlights.json"))
    headline = cell(hv[0], 1) if hv else ""
    paras = [cell(r, 0) for r in hv[3:] if cell(r, 0)]
    put("board", "highlights", {"headline": headline, "paragraphs": paras})

    # --- Attendance warning levels (G = level name, K = points that trigger it) ---
    levels = []
    for r in load(os.path.join(src, "attendance.json")):
        nm, at = cell(r, 0), num(cell(r, 4))
        if nm and at is not None: levels.append({"name": nm, "at": at})
    put("board", "attendance", {"levels": sorted(levels, key=lambda l: l["at"])})

    # --- Point balances and prizes (Tiers tab) ---
    pv = load(os.path.join(src, "points.json"))
    balances, prizes = [], []
    for r in pv[2:]:
        if cell(r, 0):
            balances.append({"name": cell(r, 0), "earned": num(cell(r, 1)) or 0,
                             "spent": num(cell(r, 2)) or 0, "balance": num(cell(r, 3)) or 0})
        if cell(r, 5):
            prizes.append({"prize": cell(r, 5), "cost": num(cell(r, 6)), "details": cell(r, 7)})
    put("board", "points", {"balances": balances, "prizes": prizes})

    # --- PINs ---
    pins = {}
    for r in load(os.path.join(src, "pins.json"))[1:]:
        who, pin = cell(r, 0), re.sub(r"\D", "", cell(r, 1))
        if who and 1 <= len(pin) <= 4: pins[who.lower()] = pin.zfill(4)  # Sheets drops leading zeros
    mgr = pins.get("manager")
    mgr_key = pin_key("manager", mgr) if mgr else None

    # --- Agents ---
    # Rep tab layout (label-based, so rows can move):
    #   a row whose column A is "Attendance Points" -> value in column B
    #   a header row whose column A starts with "Week" -> metric names in B, C, D...
    #   optional row right under it whose column A is "Target" -> weekly targets
    #   every row below with a date in column A -> one week of values
    #   (row 7 is the live week, built from formulas; finished weeks come from the History tab)
    def parse_date(t):
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m/%d"):
            try: return datetime.datetime.strptime(t, fmt)
            except ValueError: pass
        return None

    # --- History tab: one row per rep per finished week, typed each Monday ---
    #   header row 1: Week Of | Rep | <same metric names as the rep tabs> | Tier | Reward Pts
    #   Rep can be the tab name (Joe) or the full name (Joseph Black).
    hv_rows = load(os.path.join(src, "history.json"))
    hh = [cell(hv_rows[0], j) for j in range(len(hv_rows[0]))] if hv_rows else []
    def hcol(*starts):
        return next((j for j, h in enumerate(hh) if h.lower().startswith(starts)), None)
    c_week, c_rep, c_tier, c_rew = hcol("week"), hcol("rep", "agent", "name"), hcol("tier"), hcol("reward")
    metric_cols = [j for j, h in enumerate(hh) if h and j not in (c_week, c_rep, c_tier, c_rew)]
    who = {}
    for tab, full in AGENTS.items():
        for n in (tab, full, full.split()[0]): who.setdefault(n.lower(), tab)
    hist = {tab: [] for tab in AGENTS}
    if c_week is not None and c_rep is not None:
        for r in hv_rows[1:]:
            wk, tab = cell(r, c_week), who.get(cell(r, c_rep).lower())
            if not wk or not tab: continue
            hist[tab].append({"week": wk, "_d": parse_date(wk),
                "byName": {hh[j].lower(): cell(r, j) for j in metric_cols},
                "tier": cell(r, c_tier) if c_tier is not None else "",
                "reward": num(cell(r, c_rew)) if c_rew is not None else None})
    # Points Log rows: only weeks where every rep has a Tier typed in
    def week_label(d):
        e = d + datetime.timedelta(days=6)
        return f"{d:%B} {d.day}, {d.year} - {e:%B} {e.day}, {e.year}"
    plog, weeks_seen = [], {}
    for tab, rows in hist.items():
        for h in rows:
            if h["_d"] and h["tier"]: weeks_seen.setdefault(h["_d"], {})[tab] = h
    for d in sorted(weeks_seen):
        if len(weeks_seen[d]) < len(AGENTS): continue
        for tab in AGENTS:
            h = weeks_seen[d][tab]; rw = h["reward"]
            plog.append([week_label(d), AGENTS[tab], h["tier"],
                         int(rw) if rw is not None and rw == int(rw) else rw])
    json.dump(plog, open(os.path.join(os.path.dirname(os.path.abspath(src)), "points_log_rows.json"), "w"))
    for tab, full in AGENTS.items():
        key = tab.lower()
        g = load(os.path.join(src, f"tab_{tab}.json"))
        att, metrics, hdr = None, None, None
        for i, r in enumerate(g):
            a = cell(r, 0).lower()
            if a.startswith("attendance"): att = num(cell(r, 1))
            if hdr is None and a.startswith("week"): hdr = i
        names, targets, raw_t, weeks = [], [], [], []
        if hdr is not None:
            ncols = max(len(g[hdr]) - 1, 0)
            names = [cell(g[hdr], j) for j in range(1, ncols + 1)]
            targets, raw_t = [None] * ncols, [""] * ncols
            for r in g[hdr + 1:]:
                a = cell(r, 0)
                if not a: continue
                if a.lower().startswith(("target", "minimum", "goal")):
                    targets = [num(cell(r, j)) for j in range(1, ncols + 1)]
                    raw_t = [cell(r, j) for j in range(1, ncols + 1)]
                    continue
                vals = [cell(r, j) for j in range(1, ncols + 1)]
                if not any(vals): continue
                weeks.append({"week": a, "values": [num(v) for v in vals], "display": vals,
                              "_d": parse_date(a)})
        if not any(names) and hist[tab]:
            names = [hh[j] for j in metric_cols]
            targets, raw_t = [None] * len(names), [""] * len(names)
        # finished weeks from the History tab come first so they win over a live row with the same date
        final = []
        for h in hist[tab]:
            vals = [h["byName"].get(n.lower(), "") for n in names]
            final.append({"week": h["week"], "values": [num(v) for v in vals], "display": vals,
                          "_d": h["_d"], "final": True, "tier": h["tier"], "reward": h["reward"]})
        seen, uniq = set(), []
        for w in final + weeks:
            k = w["_d"] or w["week"]
            if k in seen: continue
            seen.add(k); uniq.append(w)
        weeks = uniq
        # newest week first, whatever order the rows are in
        if weeks and all(w["_d"] for w in weeks): weeks.sort(key=lambda w: w["_d"], reverse=True)
        for w in weeks: w.pop("_d")
        if any(names):
            metrics = {"names": names, "targets": targets, "targetDisplay": raw_t, "weeks": weeks}
        pin = pins.get(full.lower()) or pins.get(key)
        private = {"attendance": att, "metrics": metrics}
        put("agents", key, {"id": key, "tab": tab, "name": full, "order": list(AGENTS).index(tab),
            "lock": seal(pin_key(key, pin), private) if pin else None,
            "mlock": seal(mgr_key, private) if mgr_key else None})

    put("board", "meta", {"syncedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
    site = {"v": 2, "hasManager": bool(mgr_key), "logUrl": log_url, "board": docs["board"],
            "agents": sorted(docs["agents"].values(), key=lambda a: a["order"])}
    tmp = site_path + ".tmp"
    json.dump(site, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, site_path)
    print(f"wrote {site_path}: {len(docs['board'])} board sections, {len(site['agents'])} agents, "
          f"{sum(1 for a in site['agents'] if a['lock'])} with PINs, manager PIN {'set' if mgr_key else 'missing'}, "
          f"{len({r[0] for r in plog})} complete History week(s), login log {'on' if log_url else 'off'}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
