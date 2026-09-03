"""Postings that are closed to anyone needing visa sponsorship.

Separate from locparse's *location* eligibility: a job can be squarely in the right country and still be
unreachable because it demands US citizenship, permanent residence, or a security clearance (which in
practice requires citizenship). For someone on F-1 OPT those are hard blockers, so `jobs.py html` and
`rank.py` drop them the same way they drop out-of-country postings.

`blocks(jd, title)` -> (bool, evidence). Evidence is the matched phrase, so a wrong call is easy to spot.

Deliberately narrow: it fires only on phrases that state a *requirement*, never on the presence of a word.
"US citizens and green card holders are encouraged to apply" is not a bar, and neither is "no security
clearance required" — both used to be caught by the obvious regex, so both are negation-guarded below.
"""
import re

# Requirement-shaped statements only. Each alternative has to carry its own "must/only/required" weight.
_RULES = [
    ("no sponsorship", re.compile(r"""(
        (?:not|unable|cannot|can't|won't|will\s+not|do(?:es)?\s+not|are\s+not)\s
            (?:\w+\s){0,6}?(?:able\s+to\s+|in\s+a\s+position\s+to\s+)?sponsor
      | \bno\s+(?:visa\s+|employment\s+|immigration\s+)?sponsorship\b
      | sponsorship\s+(?:is\s+)?not\s+(?:available|offered|provided)
      | not\s+eligible\s+for\s+(?:visa\s+|immigration\s+)?sponsorship
      | without\s+(?:the\s+need\s+for\s+)?(?:any\s+)?(?:visa\s+|employer\s+|company\s+)?sponsorship
      | (?:not|does\s+not)\s+(?:now\s+or\s+in\s+the\s+future\s+)?require\s+sponsorship
      | require\s+sponsorship\s+(?:now\s+or\s+in\s+the\s+future)
    )""", re.I | re.X)),

    ("citizenship required", re.compile(r"""(
        (?:must\s+be|require[sd]?|only)\s(?:\w+\s){0,3}?U\.?\s?S\.?\s?(?:A\.?\s)?citizens?
      | U\.?\s?S\.?\s?citizenship\s+(?:is\s+)?(?:required|mandatory|a\s+requirement)
      | citizenship\s+required
      | U\.?\s?S\.?\s?citizens?\s+(?:only|and\s+(?:GC|green\s+card))
      | (?:green\s+card|GC|permanent\s+resident)s?\s+(?:holders?\s+)?only
      | (?:citizens?|GC)\s+(?:holders?\s+)?(?:or|and)\s+(?:GC|green\s+card|permanent\s+resident)
            [^.\n]{0,20}\bonly
      | no\s+H-?1\s?B?\s+(?:visa\s+)?(?:holders?|candidates?)
      | can'?t\s+work\s+with\s+H-?1B
    )""", re.I | re.X)),

    ("security clearance", re.compile(r"""(
        (?:active|current|existing|obtain(?:ing)?|maintain|eligible\s+for|able\s+to\s+obtain)
            [^.\n]{0,30}\b(?:security|secret|top[-\s]secret|TS/SCI|public\s+trust)\s+clearance
      | \b(?:security|secret|top[-\s]secret|TS/SCI|public\s+trust)\s+clearance\s+
            (?:is\s+)?(?:required|mandatory|a\s+(?:must|requirement))
      | (?:must|will\s+need\s+to)\s+(?:\w+\s){0,3}?(?:hold|possess|obtain)[^.\n]{0,30}clearance
      | \brequires?\s+(?:an?\s+)?(?:active\s+)?(?:security|secret|public\s+trust)\s+clearance
    )""", re.I | re.X)),
]

# Phrases that look like a bar but aren't. Checked against the window around each match.
_NEGATED = re.compile(r"""(
    no\s+(?:security\s+|secret\s+)?clearance\s+(?:is\s+)?(?:required|needed|necessary)
  | (?:does\s+not|doesn't|not)\s+require\s+(?:an?\s+)?(?:security\s+|secret\s+)?clearance
  | clearance\s+(?:is\s+)?not\s+required
  | (?:we|employer)\s+(?:do|does|will|can)\s+sponsor
  | sponsorship\s+(?:is\s+)?available
  | (?:citizens?|applicants?)[^.\n]{0,30}encouraged\s+to\s+apply
  | equal\s+opportunity[^.\n]{0,40}(?:citizenship|national\s+origin)
)""", re.I | re.X)


def blocks(jd, title=""):
    """True when the posting states a citizenship, permanent-residence or clearance requirement."""
    text = f"{title or ''}. {jd or ''}"
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    for label, rx in _RULES:
        for m in rx.finditer(text):
            lo, hi = max(0, m.start() - 90), min(len(text), m.end() + 90)
            if _NEGATED.search(text[lo:hi]):
                continue
            return True, f"{label}: …{text[max(0, m.start() - 40):m.end() + 40].strip()}…"
    return False, ""


if __name__ == "__main__":
    CASES = [
        # (text, should_block)
        ("We are unable to sponsor or take over sponsorship of an employment Visa at this time.", True),
        ("This position is not eligible for visa sponsorship now or in the future.", True),
        ("Must be eligible to work in the United States without visa sponsorship.", True),
        ("This position is not available for visa sponsorship or relocation assistance.", True),
        ("Duration: 12+ months US citizen or GC holder candidates only", True),
        ("SDET III // US Citizens and GC Candidates Only", True),
        ("CAN'T WORK WITH H1B OR C2C candidates", True),
        ("CITIZEN - OR - PERMANENT RESIDENT ALIEN (No H1 visa holders)", True),
        ("US Citizenship Required. We are seeking an Instructional Designer.", True),
        ("Must be able to obtain the appropriate government security clearance.", True),
        ("Requires an active Top-Secret clearance with polygraph.", True),
        ("Candidates must hold a current public trust clearance.", True),
        ("we are unable to sponsor work visas for this position", True),
        ("is unable to sponsor or take over sponsorship of employment visas (such as H-1B, TN, or STEM OPT extensions)", True),
        # five words between the negation and "sponsorship" — the window was {0,4} until M3's posting slipped through
        ("At this time, we are unable to provide current or future sponsorship for employment authorization.", True),
        ("PNC will not provide sponsorship for employment visas or participate in STEM OPT for this position.", True),
        # must NOT block
        ("H1-B Visa sponsorship to candidates who are on F1 visa status.", False),
        ("We sponsor H-1B and green cards for the right candidate.", False),
        ("No security clearance is required for this role.", False),
        ("This position does not require a security clearance.", False),
        ("Visa sponsorship is available for exceptional candidates.", False),
        ("We are an equal opportunity employer and do not discriminate on the basis of citizenship or national origin.", False),
        ("US citizens and green card holders are encouraged to apply, as are candidates requiring sponsorship.", False),
        ("Good Business Good Citizen is one of our values.", False),
        ("Work with our clearance team to resolve customer issues.", False),
        ("You will own the release gate and secret management for the pipeline.", False),
        ("Selenium, Cucumber BDD and Jenkins experience required.", False),
    ]
    fails = 0
    for text, want in CASES:
        got, ev = blocks(text)
        if got is not want:
            fails += 1
            print(f"FAIL want={want} got={got}: {text[:72]}")
            if ev: print(f"      matched -> {ev[:110]}")
    print(f"{len(CASES) - fails}/{len(CASES)} passed" if not fails else f"{fails} FAILED of {len(CASES)}")
    raise SystemExit(1 if fails else 0)
