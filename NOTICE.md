# Attribution

This project builds on **[open-jobs](https://github.com/elliottdehn/open-jobs)** by Elliott Dehn,
released under CC0 1.0 Universal (public domain dedication). CC0 imposes no conditions, so this
notice is courtesy rather than obligation.

Carried over from that project, some with local modifications:

| File | Origin |
|---|---|
| `tools/jobs.py` | upstream, unmodified in substance |
| `tools/locparse.py` | upstream, **modified** — see below |
| `tools/salary.py`, `tools/seniority.py` | upstream |
| `tools/city_countries.json` | upstream |
| `tools/test_locparse.py`, `tools/test_seniority.py` | upstream, extended |

Written for this project:

| File | |
|---|---|
| `digest.py` | selection, ranking, rendering, delivery, ledger |
| `fresh.py` | two-pass recency + eligibility filter |
| `tools/live.py` | live job-board readers for nine ATS platforms |
| `tools/workauth.py` | work-authorization and sponsorship-bar detection |
| `tools/test_live.py` | 32 pure + 4 networked cases |

## Changes to `tools/locparse.py`

1. `_fallback_places()` — n-gram scan over the location string when normal parsing yields no
   country, for office-campus strings (`India Office - Hyderabad`, `IND_Chennai`). Deliberately does
   **not** read a bare `IN` as India: it is Indiana at least as often (`IN_Indianapolis_HQ`).
2. `eligibility()` — job-description phrases previously *added* countries to a posting that already
   stated its location, so company boilerplate ("a U.S.-based investment company") made offshore
   roles look US-eligible. The posting's own location now wins.
3. `_ambiguous_code_country()` — a bare two-letter token that is both a US state code and an ISO
   country code resolves to the country only when a city in the same string corroborates it.
4. `_split()` — collapses "Remote Friendly" / "Remote-first" before segmenting, so the trailing word
   cannot be resolved as a city.
5. Added Indian city aliases that job boards actually write (Pondicherry, Trivandrum, Cochin, Vizag).

The upstream test suite grew from 112 to 131 cases alongside these.

## Data

The job corpus is fetched at runtime from the open-jobs backend and is not redistributed here.
