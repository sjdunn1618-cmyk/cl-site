#!/usr/bin/env python3
"""Pull aggregate saves/cancels and renewals from FieldRoutes (READ-ONLY) and
update board.saves, board.renewals and meta in data.json. Nothing else is touched.
Only per-rep totals are written. No customer data is kept or written.

Secrets come from env vars FR_KEY_<n> / FR_TOKEN_<n> (n = office host number).
Run:  python fr_pull.py            (live)
      python fr_pull.py --fixture DIR   (offline test)
"""
import os, sys, json, re, time, datetime as dt, urllib.request, urllib.parse
from zoneinfo import ZoneInfo

HOSTS = [1, 2, 3, 4, 5, 6, 8, 9, 10]          # brooksN.pestroutes.com, Dallas (7) excluded
IC_SAVE, OOC_SAVE = {501, 504}, {505, 506}     # 507 (Save From CXL - Pushed), 508 excluded
SAVE_TYPES = sorted(IC_SAVE | OOC_SAVE)
EXCLUDE_CANCEL = {"Renewal", "Bad Debt", "Cancelled Through Email", "Initial Appointment Canceled"}
IC_CANCEL, OOC_CANCEL = "In Contract Cancel", "Out of Contract Cancel"
RENEWAL_SRC = {  # source name (lowercase) -> bucket. Rate Raise / ZOLD count toward totals only.
    "renewal - in contract": "ic", "renewal - out of contract": "ooc",
    "renewal - no contract": "nc",
    "rate raise renewal": "oth", "zold - renewal": "oth", "zoldz - renewal": "oth"}
CFG = json.load(open("range_config.json")) if os.path.exists("range_config.json") else {}

def norm(s): return re.sub(r"\s+", " ", s or "").strip()

def roster_name(name, roster):
    names = {norm(r).casefold(): r for r in roster}
    return names.get(norm(name).casefold())

# ---------------- date ranges ----------------
def ranges(today=None):
    t = today or dt.datetime.now(ZoneInfo("America/Denver")).date()
    mon = t - dt.timedelta(days=t.weekday())
    cur = CFG.get("current") or [mon.isoformat(), t.isoformat()]
    ytd = CFG.get("ytd") or [dt.date(t.year, 1, 1).isoformat(), t.isoformat()]
    return cur, ytd
def label(a, b):
    f = lambda s: dt.date.fromisoformat(s).strftime("%B %-d, %Y")
    return f"{f(a)} - {f(b)}"

# ---------------- FieldRoutes fetch (read-only: search/get only) ----------------
class FR:
    def __init__(self, n):
        self.base = f"https://brooks{n}.pestroutes.com/api/"
        self.k, self.t = os.environ[f"FR_KEY_{n}"], os.environ[f"FR_TOKEN_{n}"]
    def call(self, ep, **kw):
        assert ep.endswith(("/search", "/get")), "read-only"
        p = {"authenticationKey": self.k, "authenticationToken": self.t}
        p.update({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})
        req = urllib.request.Request(self.base + ep, urllib.parse.urlencode(p).encode())
        for i in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    result = json.load(r)
                if result.get("success") is not True:
                    raise RuntimeError(f"FieldRoutes {ep} returned an unsuccessful response")
                return result
            except Exception:
                if i == 3: raise
                time.sleep(2 * (i + 1))
    def bulk(self, ids, ep, key):
        out = []
        for i in range(0, len(ids), 1000):
            batch = ids[i:i+1000]
            response = self.call(f"{ep}/get", **{f"{ep}IDs": batch})
            rows = response.get(key)
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise RuntimeError(f"Incomplete FieldRoutes {ep}/get response")
            out += rows
        return out

def required_rows(response, key):
    rows = response.get(key)
    if not isinstance(rows, list):
        raise RuntimeError(f"Missing FieldRoutes response array: {key}")
    if response.get("count") is not None and int(response["count"]) != len(rows):
        raise RuntimeError(f"Truncated FieldRoutes response array: {key}")
    return rows

def between(a, b): return {"operator": "BETWEEN", "value": [a + " 00:00:00", b + " 23:59:59"]}

def fetch_office(n, a, b):
    """Returns minimal rows: notes [{t,e,r}] and renewal subs [{rep,src,cv,signed,da}]. No customer fields."""
    fr = FR(n)
    emp = {}
    for e in required_rows(fr.call("employee/search", includeData=1), "employees"):
        emp[str(e["employeeID"])] = norm(f"{e.get('fname','')} {e.get('lname','')}")
    ids = required_rows(fr.call("note/search", includeData=0, date=between(a, b), typeIDs=SAVE_TYPES + [16]), "noteIDs")
    notes = [{"t": int(x.get("typeID") or 0), "d": (x.get("date") or "")[:10],
              "e": norm(x.get("employeeName")), "r": x.get("cancellationReason")}
             for x in fr.bulk(ids, "note", "notes")]
    sid = required_rows(fr.call("subscription/search", includeData=0, dateAdded=between(a, b)), "subscriptionIDs")
    subs = []
    for s in fr.bulk(sid, "subscription", "subscriptions"):
        src = norm(s.get("source") or "")
        if src.lower() in RENEWAL_SRC:
            subs.append({"rep": emp.get(str(s.get("soldBy")), ""), "src": src, "cv": s.get("contractValue") or "0",
                         "ca": s.get("contractAdded"), "da": s.get("dateAdded")})
    return {"notes": notes, "subs": subs}

# ---------------- aggregation (pure, testable) ----------------
def agg_saves(notes, roster):
    c = {r: dict(s=0, c=0, si=0, ci=0, so=0, co=0) for r in roster}
    for x in notes:
        r = roster_name(x["e"], roster)
        if r not in c: continue
        if x["t"] in IC_SAVE | OOC_SAVE:
            c[r]["s"] += 1
            c[r]["si" if x["t"] in IC_SAVE else "so"] += 1
        elif x["t"] == 16 and x["r"] not in EXCLUDE_CANCEL:
            c[r]["c"] += 1
            if x["r"] == IC_CANCEL: c[r]["ci"] += 1
            if x["r"] == OOC_CANCEL: c[r]["co"] += 1
    return c

def pct(s, c): return round(100 * s / (s + c), 2) if s + c else 0.0
def ranked(rows, key):
    rows = sorted(rows, key=key, reverse=True)
    out, prev, rk = [], None, 0
    for i, r in enumerate(rows):
        k = key(r)
        if k != prev: rk, prev = i + 1, k
        out.append(dict(r, rank=rk))
    return out

def saves_board(notes, roster):
    c = agg_saves(notes, roster)
    def mk(sk, ck):
        rows = [{"name": r, "pct": round(pct(v[sk], v[ck])), "saves": float(v[sk]), "cancels": float(v[ck])} for r, v in c.items()]
        return ranked(rows, lambda x: (x["pct"], x["saves"]))
    def tot(sk, ck):
        S, C = sum(v[sk] for v in c.values()), sum(v[ck] for v in c.values())
        return {"name": "Total", "rank": None, "pct": round(pct(S, C), 1), "saves": float(S), "cancels": float(C)}
    return {"overall": mk("s", "c"), "overallTotal": tot("s", "c"),
            "inContract": mk("si", "ci"), "inContractTotal": tot("si", "ci"),
            "outContract": mk("so", "co"), "outContractTotal": tot("so", "co")}

def agg_renewals(subs, roster, a, b):
    c = {r: dict(ic=0.0, ooc=0.0, nc=0.0, oth=0.0, n=0, un=0) for r in roster}
    for s in subs:
        r = roster_name(s["rep"], roster)
        if r not in c or not (a <= (s["da"] or "")[:10] <= b): continue
        if s["ca"]:   # Signed Agreement = Yes
            c[r][RENEWAL_SRC[s["src"].lower()]] += float(s["cv"]); c[r]["n"] += 1
        else:
            c[r]["un"] += 1
    return c

def renewals_list(c):
    rows = []
    for r, v in c.items():
        tot = round(v["ic"] + v["ooc"] + v["nc"] + v["oth"], 2)
        rows.append({"name": r, "total": tot, "ic": round(v["ic"], 2), "ooc": round(v["ooc"], 2), "nc": round(v["nc"], 2),
                     "count": float(v["n"]), "acv": round(tot / v["n"]) if v["n"] else 0.0,
                     "unsigned": float(v["un"]),
                     "signedPct": round(100 * v["n"] / (v["n"] + v["un"]), 2) if v["n"] + v["un"] else 0.0})
    return ranked(rows, lambda x: x["total"])

# ---------------- main ----------------
def load_hours():
    """Reserved hook for a future Homebase hours feed: hours.json {"Rep Name": hours}.
    Not called today. Rev/hr and tiers (data.json board.tiers) are left exactly as the sheet-driven sync wrote them."""
    return json.load(open("hours.json")) if os.path.exists("hours.json") else {}

def main():
    data = json.load(open("data.json"))
    roster = list(dict.fromkeys(a["name"] for a in data.get("agents", []))) or \
             [r["name"] for r in data["board"]["saves"]["overall"]]
    cur, ytd = ranges()
    today = dt.datetime.now(ZoneInfo("America/Denver")).date().isoformat()
    a0 = min(cur[0], ytd[0], today); b0 = max(cur[1], ytd[1], today)
    if "--fixture" in sys.argv:
        d = sys.argv[sys.argv.index("--fixture") + 1]
        offices = [json.load(open(f"{d}/h{n}.json")) for n in HOSTS]
        offices = [{"notes": [{"t": x["t"], "e": x["e"], "r": x["r"], "d": x["d"][:10]} for x in o["notes"]], "subs": o["subs"]} for o in offices]
    else:
        if any(not os.environ.get(f"FR_{kind}_{n}") for n in HOSTS for kind in ("KEY", "TOKEN")):
            sys.exit("missing FR_KEY/FR_TOKEN secrets for some offices")
        offices = [fetch_office(n, a0, b0) for n in HOSTS]
    all_notes = [x for o in offices for x in o["notes"]]
    if any(not x.get("d") for x in all_notes):
        raise RuntimeError("Note date missing; refusing to publish")
    notes = [x for x in all_notes if cur[0] <= x["d"] <= cur[1]]
    subs = [x for o in offices for x in o["subs"]]
    # saves/cancels are fetched for the full span; range them by office pull (live pulls use cur range)
    sv = saves_board(notes, roster)
    bd = data["board"]
    bd["saves"] = dict(range=label(*cur), **sv)
    rc, ry = agg_renewals(subs, roster, *cur), agg_renewals(subs, roster, *ytd)
    def tot(c): return {"total": sum(v["ic"] + v["ooc"] + v["nc"] + v["oth"] for v in c.values()), "n": sum(v["n"] for v in c.values()), "un": sum(v["un"] for v in c.values())}
    def tv(c):
        t = tot(c); return {"name": "Total", "rank": None, "total": round(t["total"], 2), "ic": round(sum(v["ic"] for v in c.values()), 2), "ooc": round(sum(v["ooc"] for v in c.values()), 2), "nc": round(sum(v["nc"] for v in c.values()), 2), "count": float(t["n"]), "acv": round(t["total"] / t["n"]) if t["n"] else 0.0,
                            "unsigned": float(t["un"]), "signedPct": round(100 * t["n"] / (t["n"] + t["un"]), 2) if t["n"] + t["un"] else 0.0}
    bd["renewals"] = {"range": label(*cur), "current": renewals_list(rc), "currentTotal": tv(rc),
                      "ytd": renewals_list(ry), "ytdTotal": tv(ry)}
    synced_at = dt.datetime.now(dt.timezone.utc).isoformat()
    daily_renewals = agg_renewals(subs, roster, today, today)
    bd["day"] = {"date": today, "range": label(today, today),
                 "saves": dict(range=label(today, today), **saves_board([x for x in all_notes if x["d"] == today], roster)),
                 "renewals": {"range": label(today, today), "current": renewals_list(daily_renewals), "currentTotal": tv(daily_renewals)},
                 "syncedAt": synced_at}
    data.setdefault("meta", {})["syncedAt"] = synced_at
    data["meta"]["source"] = "fieldroutes"
    json.dump(data, open("data.json", "w"), indent=1)
    print("updated data.json: reps", len(roster), "notes", len(notes), "renewal rows", len(subs))

if __name__ == "__main__": main()
