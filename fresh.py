# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.1"]
# ///
"""Newest postings only: first seen within --hours, US-eligible, not citizenship/GC/clearance-gated.

Two passes, because the corpus cannot answer either question on its own.

Cheap pass, over `work/jobs.parquet`: recency, similarity, location. `seen_ms` is when the crawler first
saw the posting on the company's board. There is no applicant count anywhere in the data (no ATS
publishes one), so recency is the proxy: a req that appeared on the board hours ago has had no time to
accumulate applicants.

Verify pass, over the live boards (`tools/live.py`): the parquet's `jd` is a ~330-character summary, and
the sponsorship boilerplate is exactly what the summariser drops — so `blocks()` run over it is close to
blind. On 1 Sep 2026 three PNC reqs survived the cheap pass whose real descriptions said "PNC will not
provide sponsorship for employment visas or participate in STEM OPT for this position". The verify pass
re-runs `blocks()` on the full description and reads the board's own posted date, which is the only
trustworthy freshness signal: `seen_ms` tracks when the crawler first saw the *board*, so a board newly
added to the manifest dumps its entire backlog in looking hours old.

A posting whose live JD could not be read is printed with a `?` and never counted as clean —
"we could not check" must not read the same as "nothing to worry about". Use --no-verify to skip the
network pass entirely and get the old, cheaper, less trustworthy list back.
"""
import duckdb, sys, os, time, csv, argparse, datetime
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
from locparse import eligibility
from workauth import blocks
import live

ap = argparse.ArgumentParser()
ap.add_argument("--hours", type=float, default=24, help="max age by seen_ms (the crawler's first sighting)")
ap.add_argument("--min-sim", type=float, default=0.0)
ap.add_argument("--posted-days", type=float, default=0,
                help="also drop postings the board itself dates older than this (0 = keep all)")
ap.add_argument("--min-pay", type=int, default=0, help="drop when the JD names a top figure below this")
ap.add_argument("--no-verify", action="store_true", help="skip the live-JD pass (fast, but sponsorship bars slip through)")
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--out", default="")
a = ap.parse_args()

cut = (time.time() - a.hours * 3600) * 1000
con = duckdb.connect()
rows = con.execute("""SELECT title, company, location, url, jd, sim, seen_ms, ats, slug, id
                      FROM read_parquet('work/jobs.parquet')
                      WHERE seen_ms >= ? ORDER BY sim DESC""", [cut]).fetchall()

seen, out, drop = set(), [], {"dup": 0, "loc": 0, "workauth": 0, "sim": 0, "live": 0, "stale": 0, "pay": 0}
for t, c, loc, url, jd, sim, ms, ats, slug, jid in rows:
    k = (t or "", c or "")
    if k in seen: drop["dup"] += 1; continue
    seen.add(k)
    if sim < a.min_sim: drop["sim"] += 1; continue
    if eligibility("United States", loc or "", jd or "", t or "")[0] is False: drop["loc"] += 1; continue
    if blocks(jd or "", t or "")[0]: drop["workauth"] += 1; continue
    out.append({"title": t, "company": c, "location": loc, "sim": round(sim, 3),
                "age_h": round((time.time() - ms / 1000) / 3600, 1), "url": url,
                "posted": "", "pay": "", "checked": "-", "why": "",
                "_ats": ats, "_slug": slug, "_id": jid})

if not a.no_verify and out:
    print(f"verifying {len(out)} live postings…", file=sys.stderr)
    def check(r):
        d = live.fetch(r["_ats"], r["_slug"], r["_id"], r["url"])
        r["posted"] = d["posted"] or ""
        if not d["ok"]:
            r["checked"], r["why"] = "?", d["note"]
            return r
        b, why = blocks(d["jd"], r["title"] or "")
        pay = live.pay_hint(d["jd"])
        r["pay"] = pay or ""
        r["checked"], r["why"] = ("BLOCKED" if b else "ok"), (why if b else "")
        return r
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        out = list(ex.map(check, out))

    today = datetime.date.today()
    kept = []
    for r in out:
        if r["checked"] == "BLOCKED": drop["live"] += 1; continue
        if a.posted_days and r["posted"]:
            if (today - datetime.date.fromisoformat(r["posted"])).days > a.posted_days:
                drop["stale"] += 1; continue
        if a.min_pay and r["pay"] and r["pay"] < a.min_pay: drop["pay"] += 1; continue
        kept.append(r)
    out = kept

parts = [f"{drop['loc']} not US-eligible", f"{drop['workauth']} citizenship/GC/clearance",
         f"{drop['dup']} duplicates", f"{drop['sim']} below sim {a.min_sim}"]
if not a.no_verify:
    parts.append(f"{drop['live']} sponsorship-barred in the live JD")
    if a.posted_days: parts.append(f"{drop['stale']} posted over {a.posted_days:g}d ago")
    if a.min_pay: parts.append(f"{drop['pay']} under ${a.min_pay:,}")
print(f"{len(rows)} postings first seen in the last {a.hours:g}h; {len(out)} survive "
      f"(dropped {', '.join(parts)})\n")

for r in out:
    flag = {"ok": "✓", "?": "?", "-": " "}[r["checked"]]
    when = f"posted {r['posted']}" if r["posted"] else f"seen {r['age_h']:.0f}h ago"
    pay = f" | ${r['pay']:,}" if r["pay"] else ""
    print(f"{flag} {r['sim']:.3f} | {when:18} | {r['title'][:52]:52} | {(r['company'] or '')[:22]:22} | {(r['location'] or '')[:26]}{pay}")
    print(f"    {r['url']}" + (f"   [unverified: {r['why']}]" if r["checked"] == "?" else ""))

unchecked = sum(1 for r in out if r["checked"] == "?")
if unchecked:
    print(f"\n{unchecked} marked ? — the live JD could not be read, so their sponsorship terms are UNKNOWN, not clean.")

if a.out:
    cols = ["title", "company", "location", "sim", "age_h", "posted", "pay", "checked", "url"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(out)
    print(f"\n-> {a.out}")
