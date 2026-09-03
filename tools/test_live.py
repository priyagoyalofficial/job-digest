# /// script
# requires-python = ">=3.10"
# ///
"""URL-shape and parsing tests for live.py: `uv run tools/test_live.py`. Plain asserts, no framework.

Only the pure parts. The resolvers themselves need the network, so they get a smoke run instead:
`uv run tools/test_live.py --net` fetches four postings whose answers were confirmed by hand on
1 Sep 2026 — two that must come back sponsorship-blocked and two that must come back clean.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from live import endpoint, _iso, pay_hint, _phenom_ddo
from workauth import blocks

fails = 0

EP = [  # (ats, slug, id, url, expected kind, expected request URL)
    ("greenhouse", "m3", "7769973003", "https://job-boards.greenhouse.io/m3/jobs/7769973003",
     "greenhouse", "https://boards-api.greenhouse.io/v1/boards/m3/jobs/7769973003?content=true"),
    # embedded board: the snapshot URL is the company's own page, the API still keys off ats/slug/id
    ("greenhouse", "tracelinkinc", "5086199007", "https://www.tracelink.com/about/culture-and-careers/jobs?gh_jid=5086199007",
     "greenhouse", "https://boards-api.greenhouse.io/v1/boards/tracelinkinc/jobs/5086199007?content=true"),
    ("lever", "jobgether", "e26f46cb", "https://jobs.lever.co/jobgether/e26f46cb",
     "lever", "https://api.lever.co/v0/postings/jobgether/e26f46cb"),
    ("workable", "enfos-inc", "1E257E3456", "https://apply.workable.com/j/1E257E3456",
     "workable", "https://apply.workable.com/api/v1/accounts/enfos-inc/jobs/1E257E3456"),
    ("smartrecruiters", "ComtechLLC2", "743999652168795", "https://jobs.smartrecruiters.com/ComtechLLC2/743999652168795",
     "smartrecruiters", "https://api.smartrecruiters.com/v1/companies/ComtechLLC2/postings/743999652168795"),
    ("recruitee", "hudsonmanpower", "2672181", "https://hudsonmanpower.recruitee.com/o/qa-automation-engineer-iv",
     "recruitee", "https://hudsonmanpower.recruitee.com/api/offers/2672181"),
    # workday: tenant is the host's first label, site the first path segment, and the /job/... tail is kept whole
    ("workday", "oclc.wd1.myworkdayjobs.com", "/job/Dublin-OH--Hybrid/Principal-Test-Engineer_R0003913",
     "https://oclc.wd1.myworkdayjobs.com/OCLC_Careers/job/Dublin-OH--Hybrid/Principal-Test-Engineer_R0003913",
     "workday", "https://oclc.wd1.myworkdayjobs.com/wday/cxs/oclc/OCLC_Careers/job/Dublin-OH--Hybrid/Principal-Test-Engineer_R0003913"),
    # phenom: the snapshot's /us/en/ URL 302s to the careers landing page, so rebuild it — keeping the
    # seq no whole. PNC answers to both the full seq no and the bare req id; Trane and State Street 410
    # on the stripped form, so stripping the tenant prefix only ever loses.
    ("phenom", "careers.pnc.com", "PNC1GLOBALR233261", "https://careers.pnc.com/us/en/job/PNC1GLOBALR233261",
     "phenom", "https://careers.pnc.com/global/en/job/PNC1GLOBALR233261/x"),
    ("phenom", "careers.tranetechnologies.com", "TRTEGLOBALJR6894EXTERNALENGLOBAL",
     "https://careers.tranetechnologies.com/us/en/job/TRTEGLOBALJR6894EXTERNALENGLOBAL",
     "phenom", "https://careers.tranetechnologies.com/global/en/job/TRTEGLOBALJR6894EXTERNALENGLOBAL/x"),
    # oracle: the site number lives in the /hcmUI/ path and is half the REST key
    ("oraclecloud", "jpmc.fa.oraclecloud.com", "210784297",
     "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210784297",
     "oraclecloud", "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
     "?expand=all&finder=ById;Id=%22210784297%22,siteNumber=%22CX_1001%22"),
    # anything unmapped falls back to scraping the URL we already have
    ("oraclecloud", "egug.fa.us2.oraclecloud.com", "26012909", "https://egug.fa.us2.oraclecloud.com/job/26012909",
     "html", "https://egug.fa.us2.oraclecloud.com/job/26012909"),
    ("eightfold", "libertymutual", "618519201941", "https://searchjobs.libertymutualgroup.com/careers/job/618519201941",
     "html", "https://searchjobs.libertymutualgroup.com/careers/job/618519201941"),
    # a workday row with an unparseable URL must not build a bogus cxs path
    ("workday", "x.wd1.myworkdayjobs.com", "", "https://x.wd1.myworkdayjobs.com/Careers", "html", "https://x.wd1.myworkdayjobs.com/Careers"),
]
for ats, slug, jid, url, want_kind, want_url in EP:
    kind, got = endpoint(ats, slug, jid, url)
    if (kind, got) != (want_kind, want_url):
        fails += 1; print(f"FAIL endpoint {ats}/{slug}: {kind} {got}\n              want {want_kind} {want_url}")

DATES = [  # boards date postings as ISO strings, epoch seconds or epoch millis
    ("2026-08-30", "2026-08-30"),
    ("2026-08-28T00:00:00.000+0000", "2026-08-28"),
    (1787737406440, "2026-08-26"),   # lever createdAt, millis
    (1787737406, "2026-08-26"),      # the same instant in seconds
    (None, None), ("", None), (0, None), ("not a date", None),
]
for raw, want in DATES:
    got = _iso(raw)
    if got != want: fails += 1; print(f"FAIL _iso({raw!r}): {got}, want {want}")

PAY = [  # the top annual figure named, or None
    ("Annual Base Salary Range or Hourly Base Pay Range: $97,406.66 - $156,200.00", 156200),
    ("Base salary: $180,000-$200,000 annually", 200000),
    ("Salary range in the US: $120,000 - $150,000", 150000),
    ("$140k - $175k depending on experience", 175000),
    ("$75 - $80 an hour", None),          # hourly rates are below the floor, so not mistaken for a salary
    ("a $50 million funding round", None),  # and eight-figure amounts are above the ceiling
    ("", None), (None, None),
]
for jd, want in PAY:
    got = pay_hint(jd)
    if got != want: fails += 1; print(f"FAIL pay_hint({(jd or '')[:40]!r}): {got}, want {want}")

# the brace matcher has to survive braces and escaped quotes inside the JD string it is skipping over
DDO = [
    ('x phApp.ddo = {"a": {"b": "}} \\" {"}}; phApp.other = 1', {"a": {"b": '}} " {'}}),
    ("no ddo here", None),
    ("phApp.ddo = {not json};", None),
]
for page, want in DDO:
    got = _phenom_ddo(page)
    if got != want: fails += 1; print(f"FAIL _phenom_ddo({page[:30]!r}): {got}, want {want}")

n = len(EP) + len(DATES) + len(PAY) + len(DDO)
print(f"{n - fails}/{n} passed" if not fails else f"{fails} FAILED of {n}")

if "--net" in sys.argv:
    from live import fetch
    print("\nlive smoke (verdicts confirmed by hand 1 Sep 2026):")
    SMOKE = [  # (ats, slug, id, url, must_block, label)
        ("phenom", "careers.pnc.com", "PNC1GLOBALR233261", "https://careers.pnc.com/us/en/job/PNC1GLOBALR233261",
         True, "PNC — 'will not ... participate in STEM OPT'"),
        ("greenhouse", "m3", "7769973003", "https://job-boards.greenhouse.io/m3/jobs/7769973003",
         True, "M3 — 'H-1B, OPT, STEM OPT extensions'"),
        ("workday", "oclc.wd1.myworkdayjobs.com", "/job/Dublin-OH--Hybrid/Principal-Test-Engineer_R0003913",
         "https://oclc.wd1.myworkdayjobs.com/OCLC_Careers/job/Dublin-OH--Hybrid/Principal-Test-Engineer_R0003913",
         False, "OCLC — no bar"),
        ("phenom", "careers.tranetechnologies.com", "TRTEGLOBALJR6894EXTERNALENGLOBAL",
         "https://careers.tranetechnologies.com/us/en/job/TRTEGLOBALJR6894EXTERNALENGLOBAL",
         False, "Trane — no bar"),
    ]
    for ats, slug, jid, url, must_block, label in SMOKE:
        d = fetch(ats, slug, jid, url)
        if not d["ok"]:
            fails += 1; print(f"  FAIL {label}: unreadable ({d['note']})"); continue
        b, why = blocks(d["jd"], "")
        ok = b is must_block
        if not ok: fails += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label} -> blocked={b} posted={d['posted']} "
              f"{len(d['jd'])}ch{(' | ' + why[:90]) if why else ''}")

sys.exit(1 if fails else 0)
