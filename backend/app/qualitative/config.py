"""Configuration + feature flags for the qualitative layer.

All tunables live here as named constants (no magic numbers scattered across
the collectors/classifier), matching the existing app style. Feature flags let
each source be activated sequentially: only EDGAR is on by default; the other
three are fully coded but disabled until explicitly enabled.
"""

import os
from pathlib import Path

# --- Source feature flags ---------------------------------------------------
# Toggle a collector on/off without touching code. The orchestrator
# (app/qualitative/run.py) only runs a source whose flag is True.
SOURCE_FLAGS: dict[str, bool] = {
    "edgar": True,       # SEC EDGAR 8-K filings (US-listed only)
    # Disabled: the current universe is US + ADR (NYSE/Nasdaq) only, and every
    # ticker in it is already covered by EDGAR (8-K/6-K) — ir_rss had been
    # built for non-EDGAR tickers (Japan/Korea/continental Europe), which are
    # now out of scope for the product. Collector code + stocks.ir_rss_url are
    # kept intact (not deleted): re-enable this flag if a non-EDGAR ticker is
    # ever added back to the universe.
    "ir_rss": False,     # per-ticker investor-relations RSS/Atom feed
    "newsletter": False, # Gmail newsletters (read-only OAuth) — senders not yet chosen
    "press": True,       # generalist financial press RSS
}

# Canonical source_type values, mirrored in the DB comment/columns.
SOURCE_TYPES = ("edgar", "ir_rss", "newsletter", "press")

# --- Event taxonomy (shared with the Groq prompt and the API) ---------------
# The 8 real categories + "other" (noise-rejection bucket, never persisted).
# "technical_moat" was retired: too narrative/unreliable to classify reliably
# from a structured filing source — it almost never triggered correctly on the
# 8-K/6-K items tested so far.
CATEGORIES = (
    "dated_contract",
    "m_and_a",
    "regulatory_admission",
    "guidance",
    "backlog",
    "governance_risk",
    "activist_pressure",
    "customer_concentration",
)
CATEGORY_OTHER = "other"
SENTIMENTS = ("positive", "negative", "neutral")
SEVERITIES = ("low", "medium", "high")
CONFIDENCES = ("high", "medium", "low")

# --- Tally window -----------------------------------------------------------
# The dashboard/screener/stock tally counts events over this rolling window.
# It is a COUNT of recent events, not a score, and not backtestable.
TALLY_WINDOW_DAYS = 90
# Low-confidence events stay visible in the detailed timeline but are excluded
# from the tally count (they're too weak to headline a compact badge).
TALLY_EXCLUDE_CONFIDENCE = "low"

# --- EDGAR ------------------------------------------------------------------
# SEC requires a descriptive, contactable User-Agent on every request; this is
# a hard requirement, not optional. Overridable so the deployer sets their own
# contact address rather than shipping a placeholder in the code.
EDGAR_CONTACT_EMAIL = os.environ.get("EDGAR_CONTACT_EMAIL", "moatcheck-admin@example.com")
EDGAR_USER_AGENT = f"MoatCheck/1.0 ({EDGAR_CONTACT_EMAIL})"
# SEC rate limit is 10 req/s; we stay well under it.
EDGAR_MAX_REQUESTS_PER_SECOND = 8
EDGAR_THROTTLE_SECONDS = 1.0 / EDGAR_MAX_REQUESTS_PER_SECOND
# Form types scanned. 8-K is the domestic-issuer current-report form; 6-K is
# its equivalent for foreign private issuers (ADRs — Taiwan Semi, ASML, Alibaba,
# Shopify's MJDS filings, …), who are NOT required to file 8-K at all. Without
# 6-K, ~20 ADR tickers in the universe would silently see 0 EDGAR events forever
# (they simply never file the form being scanned) — this is what surfaced the
# gap. Both are kept (not one replacing the other): a rare status-transition
# filer can have both in the same recent window.
EDGAR_RELEVANT_FORMS = ("8-K", "6-K")

# 8-K item numbers we care about (material events). Others are ignored.
# ONLY APPLIES TO 8-K — 6-K has no numbered-item taxonomy (see _relevant_items
# and collect()'s 6-K branch: every 6-K in the scan window is forwarded to Groq
# instead, since there's no item field to pre-filter on).
#   1.01 = entry into a material definitive agreement (contracts)
#   1.03 = bankruptcy or receivership
#   2.01 = completion of acquisition/disposition of assets (M&A)
#   8.01 = other events (broad; kept because litigation/regulatory often lands here)
EDGAR_RELEVANT_8K_ITEMS = ("1.01", "1.03", "2.01", "8.01")
# ticker -> CIK map, cached locally and refreshed when older than this.
EDGAR_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_TICKER_MAP_MAX_AGE_DAYS = 30
# Cap on the number of MATCHED relevant 8-Ks emitted per ticker per run (not
# the number of filings scanned — the whole recent block is always scanned, see
# edgar.collect). Bounds downstream Groq calls; newest 8-Ks are kept first.
EDGAR_MAX_FILINGS_PER_RUN = 40
# Official SEC descriptions for the 8-K item numbers we track. Injected into the
# text sent to Groq: the submissions API gives only bare item numbers, which
# carry no semantic signal on their own — the description is what lets the
# classifier tell a material agreement from an acquisition without fetching the
# (much heavier) filing body.
EDGAR_8K_ITEM_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "8.01": "Other Events",
}
# Fetch the primary document body (the 8-K .htm) and feed its text to the
# classifier, not just the metadata. One extra SEC request per matched 8-K
# (throttled, bounded by EDGAR_MAX_FILINGS_PER_RUN). Falls back cleanly to
# metadata-only if a fetch fails. Set False to keep the lightweight
# metadata-only mode (fewer SEC requests, but the classifier rejects most
# items as "other" for lack of content).
EDGAR_FETCH_DOCUMENT_BODY = True

# --- EDGAR annual/periodic filings (10-K, 10-Q, DEF 14A, 20-F) --------------
# Separate form lists from EDGAR_RELEVANT_FORMS (8-K/6-K) — handled by an
# independent code path in edgar.py, not merged with the 8-K/6-K logic: these
# are long documents (10-K often 100+ pages) where a raw head-of-document
# truncation (the 8-K/6-K approach, where the lede carries the info) would
# only capture the cover page + table of contents.
#
# ANNUAL forms (10-K, DEF 14A, 20-F) share ONE cap/loop: cadence is
# ~1/ticker/year for each, and a given ticker only ever files ONE of
# {10-K+DEF 14A} (domestic issuer) OR {20-F} (foreign private issuer) — never
# both families — so sharing a cap can't cause 20-F to crowd out 10-K or vice
# versa for any single ticker.
EDGAR_ANNUAL_FORMS = ("10-K", "DEF 14A", "20-F")
# QUARTERLY forms (10-Q) get their OWN independent cap/loop: up to 4x/year
# cadence, so a never-scanned ticker could have a full year of 10-Qs pending
# at once — a shared counter with the (rarer) annual forms could let a 10-Q
# backlog crowd out a real 10-K/DEF14A/20-F in the same run, or vice versa.
EDGAR_QUARTERLY_FORMS = ("10-Q",)

# Section dict value shape everywhere below: (regex matching the REAL section
# heading, human label, exclude_citations). `exclude_citations=True` means the
# heading has no table-of-contents entry of its own (a sub-item, not a
# top-level Item) and must instead be told apart from inline CITATIONS
# elsewhere in the document via edgar.py::_looks_like_citation (leading "– "
# or trailing closing-quote/dash) rather than the TOC-tail check.

# 10-K section markers, validated live against a real RDDT 10-K
# (rddt-20251231.htm): 10-Ks follow the SEC's standardized numbered-item
# citations, so a literal "Item N. Title" match works reliably, discriminated
# from the table of contents / cross-references via EDGAR_TOC_TAIL_RE.
EDGAR_10K_SECTIONS: dict[str, tuple[str, str, bool]] = {
    "risk_factors": (r"Item\s+1A\.\s+Risk Factors", "Risk Factors", False),
    "legal_proceedings": (r"Item\s+3\.\s+Legal Proceedings", "Legal Proceedings", False),
    "mdna": (r"Item\s+7\.\s+Management.s Discussion and Analysis", "Management's Discussion and Analysis", False),
}

# 10-Q section markers, validated live against a real RDDT 10-Q
# (rddt-20260331.htm). Item NUMBERING DIFFERS FROM THE 10-K (confirmed, not
# assumed): Legal Proceedings is "Item 1." (not "Item 3.") and MD&A is
# "Item 2." (not "Item 7.") — both under different Parts (Legal Proceedings
# under Part II, MD&A under Part I). Risk Factors happens to share the same
# "Item 1A." number as the 10-K, but is a SEPARATE pattern entry (not reused)
# since the two forms' numbering only coincides here by chance.
EDGAR_10Q_SECTIONS: dict[str, tuple[str, str, bool]] = {
    "risk_factors": (r"Item\s+1A\.\s+Risk Factors", "Risk Factors", False),
    "legal_proceedings": (r"Item\s+1\.\s+Legal Proceedings", "Legal Proceedings", False),
    "mdna": (r"Item\s+2\.\s+Management.s Discussion and Analysis", "Management's Discussion and Analysis", False),
}

# 20-F (foreign private issuer annual report — TSM, ASML, BABA, ...: NOT
# required to file a 10-K at all, same blind spot the 6-K fix addressed for
# 8-K) section markers, validated live against a real TSM 20-F
# (tsm-20251231.htm). Item numbering is COMPLETELY DIFFERENT from the 10-K,
# and formatting varies BETWEEN 20-F filers too — TSM's own table of contents
# says "OPERATING AND FINANCIAL REVIEWS AND PROSPECTS" (plural "REVIEWS"),
# while the form's official title uses singular "REVIEW"; the pattern
# tolerates both. Two distinct heading STYLES were found:
#  - Top-level items ("ITEM 5. OPERATING...", "ITEM 8. FINANCIAL
#    INFORMATION") are ALL-CAPS with a genuine table-of-contents entry (a
#    trailing page number) — same TOC discriminator as the 10-K/10-Q.
#  - "Risk Factors" is a SUB-item under "Item 3. Key Information" with NO
#    top-level TOC entry of its own — confounded instead by inline citations
#    ("Item 3. Key Information – Risk Factors”) elsewhere in the document,
#    hence exclude_citations=True for this one.
# Only validated against ONE 20-F filer (TSM) — other 20-F filers may use
# different exact wording (as TSM's own "REVIEWS" vs the official "REVIEW"
# already shows): treat this mapping as a first validated pass, not a
# guaranteed-universal one.
EDGAR_20F_SECTIONS: dict[str, tuple[str, str, bool]] = {
    "risk_factors": (r"Risk Factors", "Risk Factors", True),
    "operating_review": (r"ITEM\s+5\.\s+OPERATING AND FINANCIAL REVIEWS? AND PROSPECTS",
                          "Operating and Financial Review", False),
    "financial_information": (r"ITEM\s+8\.\s+FINANCIAL INFORMATION",
                               "Financial Information / Legal Proceedings", False),
}

# Risk Factors is disproportionately long and multi-topic (RDDT's spans
# ~206,000 chars, dozens of distinct sub-risks) — a plain head-of-section
# excerpt would almost always miss a specific sub-risk. Validated live: RDDT's
# actual customer/partner-concentration disclosure ("substantially all of the
# contract value associated with our licensing revenue is derived from two of
# our partners") sits ~73,000 chars into the section, nowhere near its head
# (confirmed present at a near-identical offset in the 10-Q too — RDDT
# restates its full Risk Factors each quarter, not just an update). These
# patterns are searched FIRST within the whole Risk Factors span; if none
# match, extraction falls back to the section head as before (see
# edgar.py::_excerpt_from).
EDGAR_RISK_FACTORS_FOCUS_PATTERNS = (
    r"(depend(?:s|ent)?\s+on|derived\s+from|reliance\s+on)[\s\S]{0,150}?"
    r"(a\s+(?:limited|small)\s+number\s+of|one\s+(?:customer|partner|supplier)|"
    r"two\s+of\s+our|single\s+(?:customer|partner))",
)
# How far past the Risk Factors heading to search for a focus-pattern match
# before giving up and falling back to the section head. Bounds the regex
# scan cost on a very long section.
EDGAR_RISK_FACTORS_SCAN_CHARS = 250_000

# DEF 14A (proxy statement) section markers. Unlike the 10-K, a proxy has NO
# standardized numbered-item system — validated live against a real GOOGL
# proxy (goog-20260424.htm): several plausible headings ("Board Composition",
# "Corporate Governance") turned out to be recurring nav-bar/bullet text, not
# section headings, and were dropped. These two DID resolve to real,
# substantive sections and are kept (same title + TOC-discriminator mechanism
# as the 10-K sections above).
EDGAR_DEF14A_SECTIONS: dict[str, tuple[str, str, bool]] = {
    "director_nominees": (r"Election of Directors\b", "Board / director nominees", False),
    "ceo_succession": (r"Management Succession Planning", "CEO / management succession", False),
}
# Capital structure / multi-class voting rights has no single reliable heading
# on a proxy statement (the richest passage found live was inside a
# shareholder proposal, not a dedicated "Capital Structure" section) — so it's
# detected by KEYWORD CO-OCCURRENCE instead of a title match: a share-class
# token near explicit voting-power/-rights language within a short window.
EDGAR_DEF14A_VOTING_PATTERN = r"Class\s+[ABC]\b[\s\S]{0,300}?(voting power|voting rights|votes per share)"

# Shared TOC-vs-real-section discriminator for 10-K / 10-Q / DEF 14A / most of
# 20-F: their tables of contents follow the same "Title <page number> <Next
# Title>" shape, so if the text immediately after a matched heading starts
# with a bare 1-3 digit number followed by a capitalized word, it's almost
# certainly a TOC line, not the section body. Validated against a real 10-K,
# 10-Q, DEF 14A, and 20-F (in each, the real section body instead continues
# directly into prose, with no bare page number).
EDGAR_TOC_TAIL_RE = r"^\s*\d{1,3}\s+[A-Z]"

# Alternate discriminator for headings with exclude_citations=True (sub-items
# with no table-of-contents entry of their own, e.g. 20-F's "Risk Factors"):
# an inline citation elsewhere in the document reads like "Item 3. Key
# Information – Risk Factors”" — preceded by an en-dash, or followed shortly
# by an en-dash/closing quote continuing the citation chain. The real heading
# has neither immediately around it. Validated live against a real 20-F.
EDGAR_CITATION_LEAD_RE = r"[–—]\s*$"
EDGAR_CITATION_TAIL_RE = r"^\s*[–—”\"]"

# Cap on ANNUAL filings (10-K, DEF 14A, 20-F) processed per ticker per run,
# counted SEPARATELY from EDGAR_MAX_FILINGS_PER_RUN (8-K/6-K only) — the two
# must never share one budget: an annual filing is extremely low-frequency
# (~1-2/ticker/year), so a busy 8-K filer in the same run must never be able
# to crowd it out by exhausting a shared counter first.
EDGAR_MAX_ANNUAL_FILINGS_PER_RUN = 2
# Cap on QUARTERLY filings (10-Q) processed per ticker per run, counted
# SEPARATELY from both EDGAR_MAX_FILINGS_PER_RUN and
# EDGAR_MAX_ANNUAL_FILINGS_PER_RUN — a never-scanned ticker can have up to a
# full year (4) of 10-Qs pending; sized to clear that backlog in one run
# without competing with either of the other two budgets.
EDGAR_MAX_QUARTERLY_FILINGS_PER_RUN = 4

# --- Press RSS --------------------------------------------------------------
# Two kinds of feeds, handled differently by press_rss.py:
#
#  1. PER-TICKER feeds: the ticker is a query parameter, so the feed is already
#     scoped to that company — no company-name filtering needed. Fetched once
#     per ticker.
#  2. GENERAL feeds: broad market wires. They carry hundreds of unrelated items,
#     so press_rss.py MUST filter each item by the company name/ticker before
#     anything reaches Groq (otherwise the daily quota is burned on noise). To
#     avoid re-downloading each general feed once per ticker, they are fetched
#     ONCE PER RUN (press_rss.prime_general_feeds(), called from run.py) and the
#     cached entries are filtered per ticker.
#
# All URLs below were validated live (HTTP 200 + valid RSS) before being added.
# Reuters (feeds.reuters.com) and Nasdaq rssoutbound were candidates but were
# dropped: Reuters retired its public RSS (DNS no longer resolves) and the
# Nasdaq feed timed out / was unreliable. Public feed URLs drift — a feed that
# starts failing is recorded as feed_status='failed', never silently ignored.
PRESS_FEEDS_PER_TICKER = {
    "yahoo_finance": "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
}

PRESS_FEEDS_GENERAL = [
    # General market wires — filtered by company name/ticker in press_rss.py.
    "https://feeds.marketwatch.com/marketwatch/marketpulse/",            # MarketWatch MarketPulse
    "https://www.investing.com/rss/news_25.rss",                        # Investing.com Stock Market News
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",  # CNBC Business
]

# Cap on press items emitted PER TICKER PER RUN, applied to BOTH feed kinds
# AFTER company-name filtering (never before — cutting pre-filter would drop
# relevant items in favor of noise). Bounds downstream Groq calls; same
# rationale as EDGAR_MAX_FILINGS_PER_RUN. A first-ever run against an
# unfiltered per-ticker feed (Yahoo returns ~20 items/ticker with no
# last_scan gate yet) would otherwise send far more candidates to Groq than
# intended for a single run. Newest items are kept first.
PRESS_MAX_ITEMS_PER_TICKER_PER_RUN = 5

# --- Groq classification ----------------------------------------------------
# Free-tier limits are PER MODEL and change over time; VERIFY at
# https://console.groq.com/docs/rate-limits before editing. The defaults below
# target llama-3.3-70b-versatile on the free tier (as of writing):
#   30 RPM · 1,000 RPD · 12,000 TPM · 100,000 TPD.
# All four are enforced (not just RPM): the effective ceiling for our request
# size is actually TPM, not RPM (see GROQ_THROTTLE_SECONDS below), and the
# daily RPD/TPD are hard-stopped via a persistent tracker so a single buggy
# run can't burn the whole day's quota.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

GROQ_MAX_REQUESTS_PER_MINUTE = 30
GROQ_MAX_REQUESTS_PER_DAY = 1000
GROQ_MAX_TOKENS_PER_MINUTE = 12_000
GROQ_MAX_TOKENS_PER_DAY = 100_000
# Stay safely under each ceiling — free-tier accounting isn't exact and
# concurrent/other same-day runs share the quota.
GROQ_QUOTA_SAFETY_FRACTION = 0.9

# Rough char->token ratio for pre-send estimation (English/French prose ≈ 4
# chars/token). Only used to (a) refuse a request we know will bust TPM, and
# (b) pace the token-per-minute limiter; the authoritative count comes from the
# response's usage field once the call returns.
GROQ_CHARS_PER_TOKEN = 4
# Upper bound on completion tokens — the JSON answer is small; capping it keeps
# the per-request token cost predictable for the TPM/TPD budgeting.
GROQ_MAX_OUTPUT_TOKENS = 400
# System-prompt + framing overhead added to every request's token estimate.
GROQ_PROMPT_OVERHEAD_TOKENS = 400

# Retry policy: BOUNDED (never an infinite loop). A transient 429/5xx is
# retried with backoff up to GROQ_MAX_RETRIES; a DAILY-quota 429 instead
# hard-stops the whole run (GroqDailyQuotaExceeded), it is not retried.
GROQ_MAX_RETRIES = 3
GROQ_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

# Cap the source text sent to Groq (cost + context); the classifier only needs
# the lede to categorize an event. A full 10-K would blow TPM on its own, so
# long texts are truncated to this many chars (logged when it happens).
GROQ_MAX_INPUT_CHARS = 4000

# Persistent daily quota / resume tracker for Groq (mirrors the Alpha Vantage
# refresh_progress.json pattern). Records date + requests_used + tokens_used so
# multiple runs in the same day share one budget and a run stops cleanly at the
# ceiling instead of discovering it via repeated 429s.
GROQ_PROGRESS_FILE = Path(
    os.environ.get(
        "GROQ_PROGRESS_FILE",
        str(Path(__file__).resolve().parents[2] / "groq_progress.json"),
    )
)

# --- Networking -------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 30
# Generic network retry backoff (collectors), mirrors supabase_client's pattern.
NETWORK_RETRY_DELAYS = (0.5, 1.0, 2.0)

# --- Local caches / config files --------------------------------------------
_CACHE_DIR = Path(
    os.environ.get(
        "QUALITATIVE_CACHE_DIR",
        str(Path(__file__).resolve().parents[2] / "qualitative_cache"),
    )
)
EDGAR_TICKER_MAP_CACHE = _CACHE_DIR / "company_tickers.json"
# List of newsletter sender addresses to follow (curated by the operator).
# See newsletter_senders.example.json for the shape; the real file is
# NEWSLETTER_SENDERS_FILE (gitignored, filled in by the operator).
NEWSLETTER_SENDERS_FILE = Path(
    os.environ.get(
        "NEWSLETTER_SENDERS_FILE",
        str(_CACHE_DIR / "newsletter_senders.json"),
    )
)


def cache_dir() -> Path:
    """Ensures the local cache directory exists and returns it."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR
