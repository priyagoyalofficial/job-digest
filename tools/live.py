"""Fetch a posting's *live* description straight from its ATS.

`work/jobs.parquet` carries a ~330-character summary in `jd`, not the posting. That is enough to rank
against and nowhere near enough to decide eligibility: every sponsorship bar lives in the boilerplate the
summariser drops. Three PNC reqs sailed through `workauth.blocks()` on 1 Sep 2026 while their real
descriptions read "PNC will not provide sponsorship for employment visas or participate in STEM OPT for
this position" — the one sentence that matters on an F-1.

So: rank on the snapshot, decide on the live posting. `fetch()` returns the full description plus the
board's own posted date, which is also the only trustworthy freshness signal — `seen_ms` is when the
crawler first saw the *board*, so a board newly added to the manifest dumps its whole backlog in looking
a few hours old.

Every resolver is the board's public JSON API where one exists, falling back to tag-stripped HTML. A
fallback that lands on a SPA shell yields no text, and that is reported as ok=False rather than as an
empty description — "we could not check" must never read the same as "nothing to worry about".
"""
import json, re, html, urllib.request, urllib.error, datetime

UA = {"user-agent": "Mozilla/5.0 (compatible; open-jobs-tools/0.1)", "accept": "application/json, text/html"}
MIN_TEXT = 400  # shorter than any real JD; below this the fetch landed on a shell or an error page


def endpoint(ats, slug, jid, url):
    """(kind, request_url) for a posting. Pure — the URL shapes are what test_live.py pins."""
    slug, jid, url = (slug or ""), (jid or ""), (url or "")
    if ats == "greenhouse" and slug and jid:
        return "greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}?content=true"
    if ats == "lever" and slug and jid:
        return "lever", f"https://api.lever.co/v0/postings/{slug}/{jid}"
    if ats == "workable" and slug and jid:
        return "workable", f"https://apply.workable.com/api/v1/accounts/{slug}/jobs/{jid}"
    if ats == "smartrecruiters" and slug and jid:
        return "smartrecruiters", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}"
    if ats == "recruitee" and slug and jid:
        return "recruitee", f"https://{slug}.recruitee.com/api/offers/{jid}"
    if ats == "ashby" and slug:
        return "ashby", f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    if ats == "workday":
        # slug is the host, jid the "/job/<loc>/<title>_<req>" tail; the JSON lives under /wday/cxs/<tenant>/<site>
        m = re.match(r"https?://([^/]+)/([^/]+)(/job/.+)$", url)
        if m:
            host, site, tail = m.groups()
            return "workday", f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}{tail}"
    if ats == "oraclecloud":
        # the /hcmUI/ page is a shell; the site number in its path is the key to the REST resource
        m = re.match(r"https?://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/(\d+)", url)
        if m:
            host, site, rid = m.groups()
            return "oraclecloud", (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                                   f"?expand=all&finder=ById;Id=%22{rid}%22,siteNumber=%22{site}%22")
    if ats == "phenom":
        # the /us/en/ URL in the snapshot 302s to the careers landing page; /global/en/job/<seq>/x renders
        # the job. Keep the seq no whole: PNC answers to both it and the bare req id, but Trane and State
        # Street 410 on the stripped form, so stripping only ever loses.
        return "phenom", f"https://{slug}/global/en/job/{jid}/x"
    return "html", url


def _get(u, timeout=25):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _strip(s):
    """Unescape *before* stripping tags, not after. Greenhouse stores its HTML escaped inside the JSON
    (`&lt;p&gt;`), so a tag-strip-then-unescape leaves live markup in the text — which is how M3's
    "unable to provide <span …>current or future</span> sponsorship" survived `workauth.blocks()` with a
    style attribute sitting in the middle of the sentence. Unescape twice: once to expose the markup,
    once for the entities inside it."""
    s = html.unescape(s or "")
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _iso(v):
    """Board date fields are ISO strings, epoch seconds or epoch millis depending on the ATS."""
    if v in (None, "", 0): return None
    if isinstance(v, (int, float)):
        return datetime.date.fromtimestamp(v / 1000 if v > 1e11 else v).isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else None


def _phenom_ddo(page):
    """phApp.ddo = {...}; — brace-matched, because the blob contains the JD and so contains every regex
    end-anchor you might reach for."""
    i = page.find("phApp.ddo")
    if i < 0: return None
    s = page.find("{", i)
    depth, instr, esc = 0, False, False
    for k in range(s, len(page)):
        c = page[k]
        if instr:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': instr = False
        elif c == '"': instr = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try: return json.loads(page[s:k + 1])
                except ValueError: return None
    return None


def fetch(ats, slug, jid, url, timeout=25):
    """-> {jd, posted, title, ok, source, note}. `ok` is False whenever the description could not be
    read, so callers can say "unverified" instead of quietly treating it as clean.

    A mapped resolver that fails falls back to scraping the snapshot URL, because a board that has
    changed its API shape is far more common than one that has changed its public page."""
    kind, req = endpoint(ats, slug, jid, url)
    d = _fetch_via(kind, req, slug, jid, timeout)
    if not d["ok"] and kind != "html" and url:
        alt = _fetch_via("html", url, slug, jid, timeout)
        if alt["ok"]:
            alt["source"] = f"{kind}->html"
            alt["posted"] = alt["posted"] or d["posted"]
            return alt
        d["note"] += f"; html fallback also failed ({alt['note']})"
    return d


def _fetch_via(kind, req, slug, jid, timeout):
    out = {"jd": "", "posted": None, "title": "", "ok": False, "source": kind, "note": ""}
    try:
        body = _get(req, timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as e:
        out["note"] = f"{kind} fetch failed ({getattr(e, 'code', None) or type(e).__name__})"
        return out
    try:
        if kind == "greenhouse":
            d = json.loads(body)
            out.update(jd=_strip(d.get("content")), title=d.get("title") or "",
                       posted=_iso(d.get("first_published") or d.get("updated_at")))
            # The API's `content` is the JD only. M3's "we are unable to provide current or future
            # sponsorship … includes H-1B, OPT, STEM OPT extensions" appears nowhere in it — it sits above
            # the application questions on the rendered board page. So union the page in: the API is
            # authoritative for dates, the page is authoritative for what the employer actually posted.
            out["jd"] += " " + _strip(_get(f"https://job-boards.greenhouse.io/{slug}/jobs/{jid}", timeout))
        elif kind == "lever":
            d = json.loads(body)
            out.update(jd=_strip(d.get("description", "") + json.dumps(d.get("lists", []))),
                       title=d.get("text") or "", posted=_iso(d.get("createdAt")))
        elif kind == "workable":
            d = json.loads(body)
            out.update(jd=_strip(" ".join(str(d.get(k) or "") for k in ("description", "requirements", "benefits"))),
                       title=d.get("title") or "", posted=_iso(d.get("published_on") or d.get("created_at")))
        elif kind == "smartrecruiters":
            d = json.loads(body)
            out.update(jd=_strip(json.dumps(d.get("jobAd", {}))), title=d.get("name") or "",
                       posted=_iso(d.get("releasedDate") or d.get("createdOn")))
        elif kind == "recruitee":
            d = json.loads(body).get("offer", {})
            out.update(jd=_strip((d.get("description") or "") + " " + (d.get("requirements") or "")),
                       title=d.get("title") or "", posted=_iso(d.get("published_at") or d.get("created_at")))
        elif kind == "ashby":
            jobs = json.loads(body).get("jobs", [])
            j = next((x for x in jobs if str(x.get("id")) == str(jid) or (jid and jid in str(x.get("jobUrl", "")))), None)
            if not j:
                out["note"] = "no longer on the ashby board"; return out
            out.update(jd=_strip(j.get("descriptionHtml") or j.get("descriptionPlain")),
                       title=j.get("title") or "", posted=_iso(j.get("publishedAt")))
        elif kind == "workday":
            j = json.loads(body).get("jobPostingInfo", {})
            out.update(jd=_strip(j.get("jobDescription")), title=j.get("title") or "",
                       posted=_iso(j.get("startDate")))
        elif kind == "oraclecloud":
            items = json.loads(body).get("items") or []
            if not items:
                out["note"] = "no longer posted on the oracle site"; return out
            j = items[0]
            out.update(jd=_strip(" ".join(str(j.get(k) or "") for k in
                                          ("ExternalDescriptionStr", "ExternalQualificationsStr", "CorporateDescriptionStr"))),
                       title=j.get("Title") or "", posted=_iso(j.get("ExternalPostedStartDate")))
        elif kind == "phenom":
            d = _phenom_ddo(body) or {}
            j = d.get("jobDetail", {}).get("data", {}).get("job", {})
            out.update(jd=_strip((j.get("description") or "") + " " + (j.get("qualifications") or "")),
                       title=j.get("title") or "", posted=_iso(j.get("postedDate")))
        else:
            out["jd"] = _strip(body)
    except (ValueError, AttributeError, KeyError, TypeError) as e:
        out["note"] = f"{kind} parse failed ({type(e).__name__})"
        return out
    if len(out["jd"]) < MIN_TEXT:
        out["note"] = f"only {len(out['jd'])} chars of text — SPA shell or a pulled posting"
        return out
    out["ok"] = True
    return out


_PAY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d+)?\s?[kK])\b")

def pay_hint(jd):
    """Highest plausible annual figure named in the JD — a floor check, not a parse. Hourly rates and
    stray dollar amounts are why this is a hint and the caller still reads the posting."""
    best = 0
    for m in _PAY.finditer(jd or ""):
        s = m.group(1).replace(",", "").lower()
        v = float(s[:-1]) * 1000 if s.endswith("k") else float(s)
        if 40_000 <= v <= 1_000_000: best = max(best, v)
    return int(best) or None
