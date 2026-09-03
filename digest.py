# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Daily job digest: refresh the corpus, pick 10 postings never sent before, email them.

Runs unattended from Windows Task Scheduler, so every step that can fail quietly is made loud
in work/digest.log instead.

Three things this script exists to get right, all of them learned the hard way:

1. `work/groups/*.json` MUST be cleared before `jobs.py fetch`. Group ids are re-minted on every
   daily rebuild of the manifest, and cmd_fetch only downloads a group when its file is absent --
   so a cached id from an earlier day silently serves yesterday's postings under a new leaf.

2. The window widens until 10 unsent postings are found. The upstream corpus rebuilds once a day
   and the daily delta is small (305 / 24 / 2 over 28-30 Aug 2026), so "everything new since
   yesterday" is routinely fewer than 10. The ledger, not the window, is what guarantees you never
   see the same req twice.

3. A posting whose live JD could not be read is NEVER mixed in with the clean ones. fresh.py marks
   those `?`; they go in their own section under their own heading, because "we could not check the
   sponsorship terms" must not look like "the sponsorship terms are fine".
"""
import csv, datetime, glob, json, os, re, smtplib, subprocess, sys
from email.message import EmailMessage
from email.utils import formataddr

# Task Scheduler hands this a cp1252 console; an em-dash in a job title would otherwise raise
# UnicodeEncodeError inside log() and take the whole run down after the work was already done.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "work")          # runtime data + logs; created on first run
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from locparse import eligibility          # noqa: E402  (needs ROOT on the path first)

LEDGER = os.path.join(WORK, "sent.json")
LOG = os.path.join(WORK, "digest.log")
CSV_OUT = os.path.join(WORK, "digest-latest.csv")
CONF = os.environ.get("JOB_DIGEST_ENV", os.path.expanduser("~/.job-digest.env"))

WANT = 10
WINDOWS = [48, 168, 720]      # hours of seen_ms to sweep, widening until WANT unsent are found
POSTED_DAYS = 30              # drop anything the board itself dates older than this
MIN_PAY = 135000


def log(msg):
    line = "{:%Y-%m-%d %H:%M:%S}  {}".format(datetime.datetime.now(), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_conf():
    """Credentials live outside the repo. work/ is gitignored, but the home dir is safer still."""
    if not os.path.exists(CONF):
        sys.exit("no config at {} -- copy .env.example there and fill it in".format(CONF))
    c = {}
    for ln in open(CONF, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            c[k.strip()] = v.strip()
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "DIGEST_TO"):
        if not c.get(k):
            sys.exit("{} is missing {}".format(CONF, k))
    if "PASTE" in c["GMAIL_APP_PASSWORD"]:
        sys.exit("{}: GMAIL_APP_PASSWORD is still the placeholder. Generate one at "
                 "myaccount.google.com/apppasswords and paste it in.".format(CONF))
    return c


def run(args, what):
    """Run a child `uv run` script from the repo root.

    Two things have to be scrubbed from the inherited environment or the child misbehaves:
    VIRTUAL_ENV, because uv would hand the child THIS script's dependency set (which is empty) and
    fresh.py would not find duckdb; and the default cp1252 stdio, because fresh.py prints a tick
    and an ellipsis, which raises UnicodeEncodeError the moment stdout is a pipe under Task
    Scheduler. Both failures look like "the filter returned nothing", which is the worst shape for
    a bug in a script whose whole job is to report an empty result honestly.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600)


def refresh():
    """Pull today's manifest and re-fetch the neighbourhood. Clearing groups/ is not optional."""
    stale = glob.glob(os.path.join(WORK, "groups", "*.json"))
    for p in stale:
        os.remove(p)
    log("cleared {} cached group files".format(len(stale)))
    r = run(["uv", "run", "tools/jobs.py", "fetch", "--top", "12"], "fetch")
    if r.returncode != 0:
        log("fetch FAILED rc={}\n{}".format(r.returncode, r.stderr[-2000:]))
        raise SystemExit(1)
    log((r.stdout.strip().splitlines() or ["fetch done"])[-1])


def sweep(hours):
    """Run the verified freshness filter over the last `hours`; return its rows."""
    cmd = ["uv", "run", "fresh.py", "--hours", str(hours),
           "--posted-days", str(POSTED_DAYS), "--min-pay", str(MIN_PAY), "--out", CSV_OUT]
    r = run(cmd, "fresh.py")
    if r.returncode != 0:
        log("fresh.py FAILED at {}h rc={}\n{}".format(hours, r.returncode, r.stderr[-2000:]))
        return []
    if not os.path.exists(CSV_OUT):
        return []
    with open(CSV_OUT, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Cosine similarity cannot carry this on its own: in the 1 Sep corpus "Senior Vice President, Product
# Design" scored 0.54 and "Senior QA Automation Engineer" scored 0.58, so a sim floor that admits the
# second admits the first. The title is the precise instrument, so the targets are matched directly.

# Unambiguously software QA. These fill the digest first.
_SOFTWARE_QA = re.compile(r"\bsdet\b|\bengineer in test\b|\bsoftware (?:test|qa|quality)|"
                          r"\b(?:qa|test|quality) automation\b|\bautomation (?:qa|test|engineer)\b|"
                          r"\bautomation architect\b|\btest(?:ing)? (?:framework|harness)\b|"
                          r"\bqe\b|\bquality engineering\b", re.I)
# Plausibly software, but the title alone cannot prove it ("Principal Test Engineer" usually is;
# "Senior Quality Engineer" at a hardware firm is not). Admitted, but ranked below the tier above.
_GENERIC_QA = re.compile(r"\bqa\b|\bquality assurance\b|\bquality (?:engineer|analyst|lead|manager|"
                         r"specialist)\w*|\btest (?:engineer|analyst|lead|manager|architect|"
                         r"specialist)\w*|\btester\b", re.I)
_SECONDARY = re.compile(r"\bbusiness (?:systems? )?analyst|\bdata analyst|\bbusiness intelligence\b|"
                        r"\bbi (?:developer|analyst|engineer)|\bdata engineer\b|"
                        r"\banalytics engineer\b|\btableau\b|\bpower ?bi\b|\breporting analyst\b", re.I)

# "Quality" outside software is a whole different profession, and it is well represented in this
# corpus: calibration, first-article inspection, supplier quality, APQP. None of it is her work.
_INDUSTRIAL = re.compile(
    r"\bcalibration\b|\binspect(?:ion|or)\b|\bfirst article\b|\bas ?9102\b|\bapqp\b|"
    r"\badvanced product quality\b|\bsupplier quality\b|\bincoming quality\b|\bcomponents? quality\b|"
    r"\bweld\w*|\bmachinist\b|\bfoundry\b|\bcasting\b|\bplating\b|\btooling\b|\bmetrolog\w*|"
    r"\bmanufactur\w*|\bproduction quality\b|\bplant quality\b|\bshop floor\b|\bwarehouse\b|"
    r"\bclinical\b|\bpharmaceutic\w*|\bgmp\b|\bgxp\b|\blaborator\w*|\bfood safety\b|\bnursing\b|"
    r"\bpatient\b|\bdental\b|\bconstruction\b|\bcivil\b|\bstructural\b|\benvironmental\b|"
    r"\bhse\b|\bqc technician\b", re.I)
# "Tester" is a false friend outside software: a SOX control tester is audit, a penetration tester is
# security. Protest/Contest are already handled by \b.
_NOT_QA = re.compile(r"\b(?:sox|control|penetration|pen|drug|material|soil)\s+test|"
                     r"test(?:ing)? (?:pilot|kitchen)|\blatest\b|\bgreatest\b|\bcontest\b", re.I)
# 12+ years in: an entry-level req is off-brief regardless of what it pays, and the pay floor cannot
# catch it because these postings usually name no salary at all.
_TOO_JUNIOR = re.compile(r"\b(?:junior|jr\.?|intern(?:ship)?|trainee|apprentice|graduate|"
                         r"entry[- ]level|co[- ]?op|fresher)\b", re.I)


def job_key(r):
    """Identity of a posting, for both the in-run dedup and the permanent ledger.

    Case-folded, because UltiPro serves the same req under two tenant spellings (smi1009kona and
    SMI1009KONA) that differ in the URL and in the company field alike -- fresh.py's (title, company)
    dedup treats them as two jobs, and a raw-URL ledger would let the same req arrive twice on
    different days."""
    return "{}|{}".format((r.get("title") or "").strip().lower(),
                          (r.get("company") or "").strip().lower())


def relevance(title):
    """3 = unmistakably software QA. 2 = QA/test that is probably software. 1 = the BA/BI/DA second
    choice. 0 = off-brief, and dropped rather than ranked last."""
    t = title or ""
    if _NOT_QA.search(t) or _TOO_JUNIOR.search(t) or _INDUSTRIAL.search(t):
        return 0
    if _SOFTWARE_QA.search(t):
        return 3
    if _GENERIC_QA.search(t):
        return 2
    if _SECONDARY.search(t):
        return 1
    return 0


def sort_key(r):
    """Primary targets first, then newest by the board's own posted date. seen_ms is not a posting
    date, and sim misleads (short keyword-dense staffing posts outscore substantive senior reqs),
    so sim is only ever the last tie-break."""
    try:
        d = datetime.date.fromisoformat(r.get("posted") or "").toordinal()
    except ValueError:
        d = 0                                  # unknown posted date sorts last, not first
    try:
        s = float(r.get("sim") or 0)
    except ValueError:
        s = 0.0
    return (-relevance(r.get("title")), -d, -s)


def pick(ledger):
    """Widen the window until WANT unsent postings are found, preferring verified-clean ones."""
    chosen_ok, chosen_unverified, used = [], [], WINDOWS[0]
    for hours in WINDOWS:
        rows = sweep(hours)
        used = hours
        # fresh.py drops a location only on an explicit False, so "can't tell" survives it: a bare
        # "Pondicherry" parses to no country at all and sailed straight into the digest. For someone
        # who needs US work authorization, unplaceable is not good enough -- require an affirmative
        # US verdict here.
        placed = [r for r in rows if eligibility("United States", r.get("location") or "")[0] is True]
        unsent, seen = [], set()
        for r in placed:
            k = job_key(r)
            if k in ledger or k in seen:
                continue
            seen.add(k)
            unsent.append(r)
        # Off-brief titles are dropped, not ranked last: a digest padded to 10 with SVP Product Design
        # is worse than a short one. Widening the window is the way to reach 10, not lowering the bar.
        on_brief = [r for r in unsent if relevance(r.get("title"))]
        chosen_ok = sorted([r for r in on_brief if r.get("checked") == "ok"], key=sort_key)
        chosen_unverified = sorted([r for r in on_brief if r.get("checked") == "?"], key=sort_key)
        log("{}h window: {} survive, {} placed in the US, {} unsent, {} on-brief "
            "({} verified, {} unreadable)".format(hours, len(rows), len(placed), len(unsent),
                                                  len(on_brief), len(chosen_ok), len(chosen_unverified)))
        if len(chosen_ok) >= WANT:
            break
    return chosen_ok[:WANT], chosen_unverified[:max(0, WANT - len(chosen_ok))], used


def money(r):
    try:
        return "${:,}".format(int(float(r.get("pay") or "")))
    except ValueError:
        return ""


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# 24% of the corpus stores the ATS hostname where the employer name belongs
# (careers.cencora.com, iqvia.wd1.myworkdayjobs.com, jpmc.fa.oraclecloud.com). The shapes are
# regular enough to recover the employer: drop the board's own domain, the Workday pod (wd3) and
# the generic prefix, and whatever single label is left is the tenant.
_ATS_DOMAIN = re.compile(
    r"^(myworkdayjobs|myworkdaysite|oraclecloud|greenhouse|greenhouse\-?io|lever|workable|"
    r"smartrecruiters|recruitee|ashbyhq|phenompeople|icims|taleo|silkroad|jobvite|breezy|paylocity|"
    r"dayforcehcm|ultipro|ukg|avature|successfactors|brassring|workforcenow|adp|"
    r"com|net|org|io|co|us|inc)$")
# trailing digits are board shards, not names: recruiting2.ultipro.com, careers3.example.com
_ATS_NOISE = re.compile(r"^(wd\d+|fa|ocs|hcm|ext|external|en|www|careers?|jobs?|apply|applicants?|"
                        r"boards?|recruiting|talent|hire|hiring|search|join|work|people)\d*$")


_UUID = re.compile(r"^[0-9a-fA-F-]{20,}$")
_SLUG_TAIL = re.compile(r"-\d+$")                    # futurex-1, acme-2 : the board's disambiguator


_ACRONYM = {"ai", "hr", "it", "qa", "bi", "us", "usa", "uk", "llc", "llp", "pwc", "ibm", "hcl"}


def _titleise(name):
    """ATS slugs are lower-case and hyphenated (mesa-quantum-systems, enfos-inc). Title-case them,
    but leave real acronyms alone -- "Enfos Inc" reads right where "Enfos INC" does not."""
    out = []
    for w in name.replace("_", "-").split("-"):
        if not w:
            continue
        out.append(w.upper() if w.lower() in _ACRONYM else w[:1].upper() + w[1:])
    return " ".join(out)


def company_name(raw, url=""):
    c = (raw or "").strip()
    # UltiPro packs host:tenant:boardId into the company field; only the host is ever a name.
    if ":" in c and "//" not in c:
        c = c.split(":", 1)[0]
    if _UUID.match(c):
        # Paylocity stores a tenant UUID as the company; the employer is a path segment
        # (/recruiting/jobs/Details/<id>/Mind-Over-Machines/<title>). Recover it or show nothing --
        # a raw UUID in a job digest is worse than a blank.
        parts = [p for p in (url or "").split("?")[0].split("/") if p]
        if "Details" in parts:
            i = parts.index("Details")
            if len(parts) > i + 2:
                return _titleise(parts[i + 2])
        return ""
    if not c:
        return ""
    if " " not in c and "." not in c:
        # a bare ATS slug: "futurex-1", "mesa-quantum-systems", "wavestrong"
        if "-" in c or "_" in c or c.islower():
            return _titleise(_SLUG_TAIL.sub("", c))
        return c
    if " " in c or "." not in c:
        return c                                     # already a real name, or nothing to fix
    labels = [x for x in c.lower().split(".") if x]
    keep = [x for x in labels if not _ATS_DOMAIN.match(x) and not _ATS_NOISE.match(x)]
    if not keep:
        # every label was board machinery (recruiting2.ultipro.com). There is no employer name in
        # here, and a blank beats printing "Recruiting2" as though it were one.
        return ""
    name = keep[0]
    return name.upper() if len(name) <= 4 else name[:1].upper() + name[1:]


def when_str(r):
    """The board's own posted date when it gave one; otherwise say plainly that this is a crawler
    sighting, not a posting date -- seen_ms tracks when the crawler first saw the board, so a newly
    added board dumps its whole backlog in looking hours old."""
    if r.get("posted"):
        return "posted " + r["posted"]
    try:
        return "first seen {:.0f}h ago".format(float(r.get("age_h") or 0))
    except ValueError:
        return ""


def block(rows, muted=False):
    out = []
    for r in rows:
        bits = [b for b in (esc(r.get("location")), money(r), when_str(r)) if b]
        colour = "#8a8a8a" if muted else "#1a1a1a"
        co = company_name(r.get("company"), r.get("url"))
        co_html = ('<div style="color:#555;font-size:14px;margin-top:4px">{}</div>'.format(esc(co))
                   if co else "")
        out.append(
            '<div style="margin:0 0 18px;padding:0 0 18px;border-bottom:1px solid #ececec">'
            '<a href="{url}" style="color:{colour};font-size:16px;font-weight:600;'
            'text-decoration:none">{title}</a>{co}'
            '<div style="color:#888;font-size:13px;margin-top:3px">{bits}</div>'
            "</div>".format(url=esc(r["url"]), colour=colour, title=esc(r["title"]), co=co_html,
                            bits=" &nbsp;&middot;&nbsp; ".join(bits)))
    return "".join(out)


def render(ok, unverified, window):
    today = "{:%d %b %Y}".format(datetime.date.today())
    html = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;'
        'margin:0 auto;padding:8px 4px">',
        '<div style="color:#888;font-size:12px;letter-spacing:.06em;text-transform:uppercase;'
        'margin-bottom:6px">Job digest &middot; {}</div>'.format(today),
        '<div style="color:#444;font-size:14px;margin-bottom:22px">{} verified US-eligible {} you '
        'have not been sent before, newest first. Screened for a ${:,} floor and against '
        'citizenship, green-card, clearance and sponsorship bars in the live description.'
        "</div>".format(len(ok), "posting" if len(ok) == 1 else "postings", MIN_PAY),
        block(ok)]
    if unverified:
        html.append(
            '<div style="margin:26px 0 14px;padding:12px 14px;background:#fdf6e3;'
            'border-left:3px solid #e0b830;font-size:13px;color:#5a4a1a">These could not be read on '
            "the live board, so their sponsorship terms are <strong>unknown</strong> -- not confirmed "
            "clean. Check before applying.</div>")
        html.append(block(unverified, muted=True))
    html.append(
        '<div style="color:#aaa;font-size:12px;margin-top:26px;border-top:1px solid #ececec;'
        'padding-top:12px">Swept postings first seen in the last {}h. Nothing here has been sent to '
        "you before.</div></div>".format(window))

    def lines(rows):
        out = []
        for r in rows:
            co = company_name(r.get("company"), r.get("url"))
            bits = [b for b in (r.get("location"), money(r), when_str(r)) if b]
            out += [r["title"] + (" -- " + co if co else ""),
                    "  " + "  ".join(bits), "  " + r["url"], ""]
        return out

    text = ["Job digest -- " + today, ""] + lines(ok)
    if unverified:
        text += ["", "-- SPONSORSHIP TERMS UNKNOWN (live JD unreadable) --", ""] + lines(unverified)
    return "".join(html), "\n".join(text)


def main():
    dry = "--dry-run" in sys.argv
    skip_refresh = "--no-refresh" in sys.argv
    conf = {} if dry else load_conf()
    log("=== digest run start ===" + (" (dry run)" if dry else ""))
    if not skip_refresh:
        refresh()
    ledger = json.load(open(LEDGER, encoding="utf-8")) if os.path.exists(LEDGER) else {}
    ok, unverified, window = pick(ledger)

    if not ok and not unverified:
        log("nothing new to send -- no email")
        return

    html, text = render(ok, unverified, window)
    if dry:
        preview = os.path.join(WORK, "digest-preview.html")
        with open(preview, "w", encoding="utf-8") as f:
            f.write(html)
        log("dry run: {} verified + {} unverified, nothing sent, ledger untouched -> {}".format(
            len(ok), len(unverified), preview))
        print("\n" + text)
        return

    msg = EmailMessage()
    n = len(ok) + len(unverified)
    msg["Subject"] = "{} new job{} -- {:%d %b}".format(n, "s" if n != 1 else "", datetime.date.today())
    msg["From"] = formataddr(("Job digest", conf["GMAIL_USER"]))
    msg["To"] = conf["DIGEST_TO"]
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(conf["GMAIL_USER"], conf["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.send_message(msg)
    log("sent {} verified + {} unverified to {}".format(len(ok), len(unverified), conf["DIGEST_TO"]))

    # Ledger is written only after the send succeeds, so a failed send re-offers the same jobs.
    stamp = datetime.date.today().isoformat()
    for r in ok + unverified:
        ledger[job_key(r)] = {"sent": stamp, "title": r["title"], "url": r["url"]}
    json.dump(ledger, open(LEDGER, "w", encoding="utf-8"), indent=1)
    log("ledger now holds {} postings".format(len(ledger)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("UNHANDLED {}: {}".format(type(e).__name__, e))
        raise
