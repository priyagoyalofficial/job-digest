# job-digest

A daily email of job postings you have not seen before — filtered for work-authorization
eligibility against the **live job description**, not a summary of it.

Built for a specific problem: searching on an F-1 OPT visa, where a posting that says "we will not
sponsor" is a waste of an application, and where that sentence is usually missing from every
summarised copy of the job description.

```
Job digest · 02 Sep 2026

10 verified US-eligible postings you have not been sent before, newest first.
Screened for a $135,000 floor and against citizenship, green-card, clearance and
sponsorship bars in the live description.

  Associate Director: QA Automation
  Gainesville, FL, USA · first seen 17h ago

  Sr QA Automation Engineer
  Spartanburg, SC · Irving, TX · Oak Brook, IL · first seen 46h ago
  ...
```

## How it works

Each run:

1. **Refreshes the corpus** — clears cached board groups, then re-fetches. Group ids are re-minted
   on every daily rebuild upstream, and the fetcher only downloads a group whose file is absent, so
   a cached id from an earlier day silently serves yesterday's postings.
2. **Filters cheaply** over the local parquet: recency, location, obvious work-authorization bars.
3. **Verifies against the live board** (`tools/live.py`) for everything that survived. This is the
   part that matters — see below.
4. **Keeps on-brief titles**, drops everything already in the ledger, and widens the window
   (48h → 168h → 720h) until it has enough.
5. **Sends**, then writes the ledger — in that order, so a failed send re-offers the same jobs.

## Why the live verification pass exists

The corpus stores a ~330-character summary of each job description. The sponsorship boilerplate is
exactly what a summariser drops. Running a work-authorization check over that summary is close to
blind: in one run, three postings passed it whose real descriptions said *"will not provide
sponsorship for employment visas or participate in STEM OPT for this position"*.

`tools/live.py` resolves greenhouse, lever, workable, smartrecruiters, recruitee, ashby, workday,
phenom and oraclecloud to their JSON APIs, falling back to scraping. Three traps it exists to
remember:

- **Greenhouse's API omits the sponsorship statement.** It sits beside the application questions, so
  the rendered board page has to be unioned in.
- **Greenhouse escapes its HTML inside the JSON.** Unescaping must happen *before* tag-stripping, or
  the markup splits the sentence and the pattern never matches.
- **Phenom wants the whole sequence number.** `TRTEGLOBALJR6894EXTERNALENGLOBAL`, not the bare req
  id — some tenants tolerate the short form, others return 410.

A posting whose live description cannot be read is marked `?` and **never counted as clean**. Those
appear in the email under their own heading: "we could not check" must not read like "nothing to
worry about".

## Two things the data will not tell you

**`seen_ms` is not a posting date.** It is when the crawler first saw the *board*. A board newly
added to the manifest dumps its entire backlog in looking hours old — verified examples include rows
"seen 20h ago" whose real dates were four months earlier. Only the board's own posted date means
anything, which is why the digest prints "posted 2026-08-30" or "first seen 46h ago" and never
conflates the two.

**There is no applicant count, and there never will be.** This crawls employer ATS boards; "among
the first 25 applicants" is a LinkedIn overlay, not something any ATS publishes. Recency is the only
available proxy.

## Ranking is done on titles, not embeddings

The corpus ships a cosine similarity against an ideal job description. It is not used for ranking,
because it does not survive contact with real postings: in one corpus *"Senior Vice President,
Product Design"* scored **0.54** and *"Senior QA Automation Engineer"* scored **0.58**. Any
threshold admitting the second admits the first. Short, keyword-dense staffing posts also outscore
substantive senior roles.

So `relevance()` matches titles into tiers, and similarity is only ever the final tie-break.
Industrial quality work — calibration, first-article inspection, APQP, supplier quality, metrology —
is excluded explicitly, because it all matches a bare `\bquality\b` and is well represented in the
corpus.

## Setup

Needs [`uv`](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
git clone https://github.com/priyagoyalofficial/job-digest
cd job-digest

# 1. Describe the job you want. This is the search's centre of gravity.
cp ideal-jd.example.md work/ideal-jd.md   # then edit it
uv run tools/jobs.py embed --file work/ideal-jd.md --title "QA Manager" --location "Remote, US"

# 2. Credentials, outside the repo so they can never be committed.
cp .env.example ~/.job-digest.env         # then fill in
```

`GMAIL_APP_PASSWORD` is a Google [app password](https://myaccount.google.com/apppasswords), not your
account password; 2-Step Verification must be on.

```bash
uv run digest.py --dry-run                # refresh, select, render — send nothing
uv run digest.py --dry-run --no-refresh   # ...and skip the ~6 minute refresh
uv run digest.py                          # the real thing
```

`--dry-run` writes `work/digest-preview.html`, the exact email body.

### Scheduling

Windows:

```powershell
.\setup-task.ps1                 # daily at 07:12
.\setup-task.ps1 -Time 06:45     # or pick your own time
```

That registers the task with the settings that actually matter on a laptop:

| | |
|---|---|
| `StartWhenAvailable` | a missed run fires next time the machine is available, instead of being skipped |
| `RestartCount 3` | retries every 10 minutes, covering a login that beats the network up |
| `ExecutionTimeLimit` | a stuck run cannot wedge the scheduler |

Several missed days produce **one** catch-up run, not one per day — which is what you want, because
the ledger means that single run sends the best postings you have not seen yet. Nothing is lost by a
missed day: the ledger is written only after a successful send.

Task Scheduler cannot wake a powered-off machine, so a laptop that stays shut for a week means no
digest that week. Run it somewhere always-on if that matters.

Linux/macOS — `12 7 * * * cd <repo> && uv run digest.py`.

## Configuration

Constants at the top of `digest.py`:

| | |
|---|---|
| `WANT` | postings per email (default 10) |
| `WINDOWS` | widening sweep, in hours (default 48 → 168 → 720) |
| `POSTED_DAYS` | drop postings the board itself dates older than this |
| `MIN_PAY` | drop when the description names a top figure below this |

Title rules are the regexes just above `relevance()`. They encode one person's target roles; edit
them for yours.

## Tests

```bash
uv run tools/test_locparse.py    # 131 cases
uv run tools/test_live.py        # 32 pure; --net adds 4 live
uv run tools/test_seniority.py
```

Location parsing is where the bugs are, and the test table is the spec. Cases worth knowing about,
each of which was a real posting that reached a draft email:

- `IN: Hyderabad` and `Hyderabad, Telengana, IN` read as the **US**, because a bare `IN` is Indiana.
- `Bogota, Colombia / Argentina (Remote Friendly)` read as the **US** — "Remote Friendly" was split
  on, and **Friendly is a town in West Virginia**.
- A bare `Pondicherry` was neither placed nor rejected, and "cannot tell" was passing as "fine".

The fixes are narrow on purpose: `Vienna, VA` must stay in Virginia (Vienna is Austrian, VA is the
Vatican) and `Dublin, CA` must stay in California.

## Licence

CC0 1.0 Universal, matching upstream. See [`NOTICE.md`](NOTICE.md) for what came from
[open-jobs](https://github.com/elliottdehn/open-jobs) and what did not.
