"""EDGAR 8-K / 6-K / 10-K / 10-Q / DEF 14A / 20-F collector (SEC filings).

Two INDEPENDENT code paths in ``collect()``:
  - 8-K / 6-K (current reports, frequent, short): see the pipeline below.
  - 10-K / 10-Q / DEF 14A / 20-F (annual or quarterly, long documents — see
    ``_collect_periodic_filing_items``): targeted section extraction instead
    of a raw head-of-document truncation, since the useful content (Risk
    Factors, Legal Proceedings, MD&A, governance/voting sections) is almost
    never near the start of a 100+ page document. A single filing can yield
    several classification items (one per resolved section). This path has
    its OWN caps, one per cadence bucket (EDGAR_MAX_ANNUAL_FILINGS_PER_RUN for
    10-K/DEF 14A/20-F, EDGAR_MAX_QUARTERLY_FILINGS_PER_RUN for 10-Q) so a busy
    8-K filer — or a 10-Q backlog — can never crowd out a real filing from
    another bucket in the same run.

Pipeline per US-listed ticker (8-K / 6-K):
  1. Resolve ticker -> CIK via the SEC ``company_tickers.json`` map (downloaded
     once, cached locally, refreshed when older than EDGAR_TICKER_MAP_MAX_AGE_DAYS).
  2. Call the SEC ``submissions`` API for that CIK.
  3. Keep filings whose form is in EDGAR_RELEVANT_FORMS (``8-K`` or ``6-K``)
     and whose ``filed_date`` is newer than the last successful scan
     (feed_status.last_success_at):
       - 8-K (domestic issuers): only kept if it touches a relevant numbered
         item (EDGAR_RELEVANT_8K_ITEMS: material agreement, acquisition,
         litigation, …).
       - 6-K (foreign private issuers — ADRs like TSM, ASML, BABA: not required
         to file 8-K at all, so without 6-K support these tickers silently saw
         0 EDGAR events forever): NO numbered-item taxonomy exists on this
         form (it's a free-form "furnish anything material" filing), so every
         6-K in the scan window is forwarded — there's no item field to
         pre-filter on. Expect a HIGHER "other" rejection rate at
         classification than for 8-K (a 6-K is often just a routine quarterly
         results announcement, not a material event) — that's the nature of
         the form, not a bug.
  4. Fetch the primary document body, strip it to plain text, and emit one
     CollectedItem per matching filing (text = metadata header + filing body,
     trimmed to GROQ_MAX_INPUT_CHARS; the primary-document URL goes in
     ``url``). Falls back to metadata-only if the body fetch fails
     (config.EDGAR_FETCH_DOCUMENT_BODY toggles the fetch off entirely).

SEC compliance: a descriptive, contactable User-Agent is sent on every request
(hard SEC requirement), and calls are throttled below the 10 req/s limit.

Only ``stocks.currency == 'USD'`` tickers are scanned (EDGAR only covers
SEC-registered filings, which for this universe means USD-priced US-listed
tickers — including ADRs); others are skipped cleanly with a logged ``stale``
feed_status.
"""

import html
import json
import logging
import re
import time
import urllib.request
from typing import Any

from app.qualitative import config
from app.qualitative.feed_status import CollectedItem, record_feed_status

logger = logging.getLogger(__name__)

SOURCE_TYPE = "edgar"

_last_call_ts: float | None = None


def _throttle() -> None:
    """Spaces SEC calls below EDGAR_MAX_REQUESTS_PER_SECOND (module-global clock)."""
    global _last_call_ts
    if _last_call_ts is not None:
        elapsed = time.monotonic() - _last_call_ts
        wait = config.EDGAR_THROTTLE_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
    _last_call_ts = time.monotonic()


def _get_json(url: str) -> Any:
    """GETs a SEC JSON endpoint with the required User-Agent + throttle + retry.

    Returns the parsed JSON, or None on persistent failure (logged, never a
    silent empty default that would look like "no filings").
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0,) + config.NETWORK_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.EDGAR_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # HTTP, JSON, network
            last_exc = exc
            logger.warning("EDGAR GET failed (attempt %d): %s — %s", attempt + 1, url, exc)
    logger.error("EDGAR GET giving up after retries: %s (%s)", url, last_exc)
    return None


def _get_text(url: str) -> str | None:
    """GETs a SEC document as decoded text (same throttle/User-Agent/retry as _get_json).

    Returns the raw response text, or None on persistent failure (logged).
    Used to fetch a filing's primary document (the 8-K .htm) so the classifier
    sees the actual filing content, not just metadata.
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0.0,) + config.NETWORK_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": config.EDGAR_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_SECONDS) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            logger.warning("EDGAR document GET failed (attempt %d): %s — %s", attempt + 1, url, exc)
    logger.error("EDGAR document GET giving up after retries: %s (%s)", url, last_exc)
    return None


# Tags whose CONTENT is not readable prose and must be dropped wholesale before
# stripping the remaining markup.
_DROP_BLOCKS_RE = re.compile(r"(?is)<(script|style|head)\b.*?</\1>")
# Inline-XBRL headers/hidden facts: modern 8-K .htm files are iXBRL and begin
# with a large block of machine-readable context (entity IDs, iso4217:USD,
# xbrli:shares, boolean facts rendered as "true true NASDAQ", …). Left in, this
# noise fills the whole GROQ_MAX_INPUT_CHARS budget and buries the actual prose,
# so it's dropped before anything else. Namespace-prefixed tags (ix:, xbrli:)
# can't use a simple backreference, hence dedicated patterns.
_DROP_IXBRL_RE = re.compile(r"(?is)<ix:header\b.*?</ix:header>|<ix:hidden\b.*?</ix:hidden>")
# Elements explicitly hidden from render (iXBRL often stashes facts here too).
_DROP_HIDDEN_RE = re.compile(r'(?is)<div[^>]*display\s*:\s*none[^>]*>.*?</div>')
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(raw: str) -> str:
    """Very simple HTML/iXBRL -> plain text: drop script/style/head + iXBRL
    header/hidden blocks, strip remaining tags, unescape entities, collapse
    whitespace. No external dependency (bs4/lxml).

    Good enough for a classifier that only needs the prose of an 8-K body — not
    a faithful render.
    """
    cleaned = _DROP_IXBRL_RE.sub(" ", raw)
    cleaned = _DROP_HIDDEN_RE.sub(" ", cleaned)
    cleaned = _DROP_BLOCKS_RE.sub(" ", cleaned)
    no_tags = _TAG_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", html.unescape(no_tags)).strip()


def _load_ticker_cik_map() -> dict[str, str]:
    """ticker (upper) -> zero-padded 10-digit CIK, from the cached SEC map.

    Downloads/refreshes the cache when missing or older than
    EDGAR_TICKER_MAP_MAX_AGE_DAYS. Returns {} on failure (logged).
    """
    cache = config.EDGAR_TICKER_MAP_CACHE
    fresh = False
    if cache.exists():
        age_days = (time.time() - cache.stat().st_mtime) / 86400.0
        fresh = age_days < config.EDGAR_TICKER_MAP_MAX_AGE_DAYS
    if not fresh:
        logger.info("EDGAR ticker->CIK map missing/stale — downloading from SEC.")
        payload = _get_json(config.EDGAR_TICKER_MAP_URL)
        if payload is not None:
            try:
                config.cache_dir()
                cache.write_text(json.dumps(payload))
            except OSError as exc:
                logger.warning("Could not cache company_tickers.json (%s).", exc)
        elif not cache.exists():
            logger.error("EDGAR ticker map unavailable and no cache — cannot resolve any CIK.")
            return {}

    try:
        raw = json.loads(cache.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("EDGAR ticker map cache unreadable (%s).", exc)
        return {}

    # Shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    mapping: dict[str, str] = {}
    for entry in raw.values():
        tkr = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str")
        if tkr and cik is not None:
            mapping[tkr] = f"{int(cik):010d}"
    return mapping


# Cached for the lifetime of a process (a single refresh run).
_ticker_cik_map: dict[str, str] | None = None


def _cik_for(ticker: str) -> str | None:
    global _ticker_cik_map
    if _ticker_cik_map is None:
        _ticker_cik_map = _load_ticker_cik_map()
    return _ticker_cik_map.get(ticker.upper())


def _last_scan_date(ticker: str) -> str | None:
    """feed_status.last_success_at (date part) for this ticker/EDGAR, or None."""
    from app.data.supabase_client import execute_with_retry, get_supabase

    try:
        rows = execute_with_retry(
            get_supabase().table("feed_status").select("last_success_at")
            .eq("ticker", ticker).eq("source_type", SOURCE_TYPE).limit(1),
            context=f"edgar last_scan {ticker}",
        ).data
    except Exception:
        logger.error("Could not read last_scan_date for %s — treating as first scan.", ticker, exc_info=True)
        return None
    if rows and rows[0].get("last_success_at"):
        return rows[0]["last_success_at"][:10]  # YYYY-MM-DD
    return None


def _relevant_items(items_str: str) -> list[str]:
    """Intersection of a filing's comma-separated item list with the items we track."""
    present = {chunk.strip() for chunk in (items_str or "").split(",")}
    return [it for it in config.EDGAR_RELEVANT_8K_ITEMS if it in present]


_TOC_TAIL_RE = re.compile(config.EDGAR_TOC_TAIL_RE)
_DEF14A_VOTING_RE = re.compile(config.EDGAR_DEF14A_VOTING_PATTERN, re.IGNORECASE)
_CITATION_LEAD_RE = re.compile(config.EDGAR_CITATION_LEAD_RE)
_CITATION_TAIL_RE = re.compile(config.EDGAR_CITATION_TAIL_RE)


def _looks_like_citation(text: str, start: int, end: int) -> bool:
    """True if the match at [start, end) looks like an inline cross-reference
    citation (e.g. 20-F's "Item 3. Key Information – Risk Factors”") rather
    than the real section heading. Used for sub-items that have no table-of-
    contents entry of their own (see EDGAR_CITATION_LEAD_RE / _TAIL_RE)."""
    before = text[max(0, start - 3): start]
    after = text[end: end + 3]
    return bool(_CITATION_LEAD_RE.search(before)) or bool(_CITATION_TAIL_RE.match(after))


def _find_real_section_start(text: str, pattern: str, exclude_citations: bool = False) -> int | None:
    """Returns the character offset right after the first REAL (non-table-of-
    contents, non-citation) match of `pattern` in `text`, or None if every
    match looks like a TOC entry, a short cross-reference (EDGAR_TOC_TAIL_RE),
    or — when `exclude_citations` is set — an inline citation
    (see _looks_like_citation)."""
    for m in re.finditer(pattern, text):
        tail = text[m.end(): m.end() + 40]
        if _TOC_TAIL_RE.match(tail):
            continue  # table-of-contents entry or short cross-reference — skip
        if exclude_citations and _looks_like_citation(text, m.start(), m.end()):
            continue  # inline citation to the section, not the section itself
        return m.end()
    return None


def _excerpt_from(text: str, start: int, focus_patterns: tuple[str, ...] = ()) -> str:
    """Builds a bounded excerpt starting at `start`.

    Without `focus_patterns`: just the next GROQ_MAX_INPUT_CHARS (the section
    head). With `focus_patterns`: searches within
    EDGAR_RISK_FACTORS_SCAN_CHARS of `start` for the first match and centers
    the excerpt there instead — a single long, multi-topic section (Risk
    Factors) can bury the relevant sub-risk far from its head (validated
    live: RDDT's customer-concentration disclosure sits ~73,000 chars into a
    ~206,000-char Risk Factors section). Falls back to the head if no focus
    pattern matches.
    """
    if focus_patterns:
        window = text[start: start + config.EDGAR_RISK_FACTORS_SCAN_CHARS]
        for pat in focus_patterns:
            m = re.search(pat, window, re.IGNORECASE)
            if m:
                center = start + max(0, m.start() - 300)
                return text[center: center + config.GROQ_MAX_INPUT_CHARS]
    return text[start: start + config.GROQ_MAX_INPUT_CHARS]


def _extract_sections_by_heading(
    text: str, sections: dict[str, tuple[str, str, bool]]
) -> list[tuple[str, str, str]]:
    """Finds the REAL (non-table-of-contents, non-citation) body of each
    named section.

    `sections` maps key -> (pattern, label, exclude_citations) — see
    config.py's section-dict docstrings for what exclude_citations means.
    Returns a list of (key, label, excerpt) for every section that resolved
    to a real heading match. "risk_factors" gets a keyword-focused excerpt
    (see _excerpt_from); every other section gets the section head. Sections
    not found are simply absent from the result (logged by the caller).
    """
    found: list[tuple[str, str, str]] = []
    for key, (pattern, label, exclude_citations) in sections.items():
        start = _find_real_section_start(text, pattern, exclude_citations)
        if start is None:
            continue
        focus = config.EDGAR_RISK_FACTORS_FOCUS_PATTERNS if key == "risk_factors" else ()
        found.append((key, label, _excerpt_from(text, start, focus)))
    return found


def _extract_voting_section(text: str) -> str | None:
    """Keyword-co-occurrence extraction for multi-class voting structure.

    Unlike the heading-based sections, this has no single reliable title (see
    config.EDGAR_DEF14A_VOTING_PATTERN docstring) — returns a window CENTERED
    on the first co-occurrence match, or None if no match at all.
    """
    m = _DEF14A_VOTING_RE.search(text)
    if not m:
        return None
    start = max(0, m.start() - 200)
    return text[start: start + config.GROQ_MAX_INPUT_CHARS]


# form -> section dict, for every form handled by _collect_periodic_filing_items.
_FORM_SECTIONS: dict[str, dict[str, tuple[str, str, bool]]] = {
    "10-K": config.EDGAR_10K_SECTIONS,
    "10-Q": config.EDGAR_10Q_SECTIONS,
    "DEF 14A": config.EDGAR_DEF14A_SECTIONS,
    "20-F": config.EDGAR_20F_SECTIONS,
}


def _collect_periodic_filing_items(
    ticker: str, stock: dict[str, Any], cik: str, form: str, filed: str, filing_url: str,
    primary_doc: str, desc: str,
) -> list[CollectedItem]:
    """Section-extraction path for 10-K / 10-Q / DEF 14A / 20-F (see module
    docstring).

    Fetches the primary document body and emits ONE CollectedItem per
    resolved section (a single filing can yield several classification
    candidates). Falls back to ONE metadata-only item if the body can't be
    fetched, or if fetched but no known section resolved — logged explicitly
    either way, never a silent empty result.
    """
    company = stock.get("name") or ticker
    header = (
        f"SEC Form {form} filed {filed} by {company} ({ticker}). "
        f"Primary document: {desc or primary_doc or 'n/a'}."
    )

    if not (config.EDGAR_FETCH_DOCUMENT_BODY and primary_doc):
        logger.info("%s: %s %s body fetch disabled — metadata-only item.", ticker, form, filed)
        return [CollectedItem(ticker=ticker, raw_text=header, published_date=filed,
                               source_type=SOURCE_TYPE, url=filing_url)]

    doc_html = _get_text(filing_url)
    body = _html_to_text(doc_html) if doc_html else ""
    if not body:
        logger.info("%s: %s %s body empty/unfetchable — falling back to metadata only.", ticker, form, filed)
        return [CollectedItem(ticker=ticker, raw_text=header, published_date=filed,
                               source_type=SOURCE_TYPE, url=filing_url)]

    sections = _FORM_SECTIONS[form]
    resolved = _extract_sections_by_heading(body, sections)
    if form == "DEF 14A":
        voting_excerpt = _extract_voting_section(body)
        if voting_excerpt:
            resolved.append(("voting_structure", "Capital structure / voting rights", voting_excerpt))

    if not resolved:
        logger.info(
            "%s: %s %s — no known section heading resolved (parsing miss or unusual layout) "
            "— falling back to metadata only.", ticker, form, filed,
        )
        return [CollectedItem(ticker=ticker, raw_text=header, published_date=filed,
                               source_type=SOURCE_TYPE, url=filing_url)]

    logger.info("%s: %s %s — %d section(s) resolved: %s.",
                ticker, form, filed, len(resolved), ", ".join(label for _, label, _ in resolved))
    items: list[CollectedItem] = []
    for key, label, excerpt in resolved:
        raw_text = f"{header} Section: {label}.\n\n{excerpt}"[: config.GROQ_MAX_INPUT_CHARS]
        items.append(
            CollectedItem(ticker=ticker, raw_text=raw_text, published_date=filed,
                          source_type=SOURCE_TYPE, url=filing_url)
        )
    return items


def collect(ticker: str, stock: dict[str, Any]) -> list[CollectedItem]:
    """Collects recent relevant 8-K/6-K filings for one ticker.

    ``stock`` is the row from ``stocks`` (currency used for the US-listed gate).
    Always records a feed_status; returns [] on skip/failure.
    """
    ticker = ticker.upper()

    # EDGAR is US filings only — skip non-USD tickers cleanly (not an error).
    if (stock.get("currency") or "").upper() != "USD":
        logger.info("%s: not USD-listed — EDGAR skipped.", ticker)
        record_feed_status(ticker, SOURCE_TYPE, "stale", last_error="not US-listed (currency != USD)")
        return []

    cik = _cik_for(ticker)
    if cik is None:
        logger.info("%s: no CIK in SEC ticker map — EDGAR skipped.", ticker)
        record_feed_status(ticker, SOURCE_TYPE, "stale", last_error="ticker not found in SEC company_tickers map")
        return []

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    payload = _get_json(url)
    if payload is None:
        record_feed_status(ticker, SOURCE_TYPE, "failed", feed_url=url, last_error="submissions API fetch failed")
        return []

    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    accession_numbers = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    primary_descs = recent.get("primaryDocDescription") or []
    items_list = recent.get("items") or []

    last_scan = _last_scan_date(ticker)
    items: list[CollectedItem] = []
    form_counts: dict[str, int] = {}

    # Iterate ALL recent filings, not a fixed slice: `recent` is chronological
    # across every form type, and high-volume filers (e.g. hundreds of Form-4
    # insider filings) would push 8-K/6-K out of any small head window entirely.
    # The whole block is already in memory (SEC caps it ~1000, no extra
    # request), so we scan it all and instead bound the number of MATCHED
    # filings emitted per run (EDGAR_MAX_FILINGS_PER_RUN) to keep downstream
    # Groq calls bounded. Newest-first ordering means the cap keeps the most
    # recent events. The cap is shared across both form types.
    for i in range(len(forms)):
        if len(items) >= config.EDGAR_MAX_FILINGS_PER_RUN:
            logger.info("%s: reached EDGAR match cap (%d) — older filings left for a later run.",
                        ticker, config.EDGAR_MAX_FILINGS_PER_RUN)
            break
        form = forms[i]
        if form not in config.EDGAR_RELEVANT_FORMS:
            continue
        filed = filing_dates[i] if i < len(filing_dates) else None
        if filed is None:
            continue
        if last_scan is not None and filed <= last_scan:
            continue  # already seen in a previous scan

        if form == "8-K":
            relevant = _relevant_items(items_list[i] if i < len(items_list) else "")
            if not relevant:
                continue
        # 6-K: no numbered-item taxonomy to pre-filter on (see module docstring)
        # — every 6-K in the window is a candidate, forwarded to Groq as-is.

        accession = accession_numbers[i] if i < len(accession_numbers) else ""
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        desc = primary_descs[i] if i < len(primary_descs) else ""
        acc_nodash = accession.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary_doc}"
            if primary_doc else
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
        )

        if form == "8-K":
            # Spell out each item number so the classifier has real semantic
            # signal (the submissions API only gives bare numbers).
            item_phrases = [
                f"Item {it} ({config.EDGAR_8K_ITEM_DESCRIPTIONS.get(it, 'unspecified')})"
                for it in relevant
            ]
            header = (
                f"SEC Form 8-K filed {filed} by {stock.get('name') or ticker} ({ticker}). "
                f"Reported items: {'; '.join(item_phrases)}. "
                f"Primary document: {desc or primary_doc or 'n/a'}."
            )
        else:
            # 6-K has no item taxonomy — the body is the only signal, so the
            # header stays minimal (form/date/company identity only).
            header = (
                f"SEC Form 6-K (foreign private issuer report) filed {filed} by "
                f"{stock.get('name') or ticker} ({ticker}). "
                f"Primary document: {desc or primary_doc or 'n/a'}."
            )

        # Fetch the primary document body so the classifier sees the actual
        # filing content (a bare "material agreement" with no details is
        # correctly rejected as noise). One extra throttled SEC request; falls
        # back to metadata-only if the fetch/strip yields nothing.
        raw_text = header
        if config.EDGAR_FETCH_DOCUMENT_BODY and primary_doc:
            doc_html = _get_text(filing_url)
            body = _html_to_text(doc_html) if doc_html else ""
            if body:
                # Header first (stable lede for dedup), then the body, trimmed to
                # the same budget the classifier uses so we don't store or hash
                # a huge blob. classify() re-truncates defensively too.
                raw_text = f"{header}\n\n{body}"[: config.GROQ_MAX_INPUT_CHARS]
            else:
                logger.info("%s: %s %s body empty/unfetchable — falling back to metadata only.",
                            ticker, form, filed)

        logger.debug("%s: matched filing form_type=%s filed=%s.", ticker, form, filed)
        form_counts[form] = form_counts.get(form, 0) + 1
        items.append(
            CollectedItem(
                ticker=ticker,
                raw_text=raw_text,
                published_date=filed,
                source_type=SOURCE_TYPE,
                url=filing_url,
            )
        )

    # --- Periodic filings (10-K/DEF 14A/20-F annual, 10-Q quarterly): SEPARATE
    # code path, SEPARATE caps (one per cadence bucket), not merged with the
    # 8-K/6-K loop above — see module docstring and config.py comments. A busy
    # 8-K filer's matches (capped above by EDGAR_MAX_FILINGS_PER_RUN) can
    # therefore never crowd out a real annual/quarterly filing in the same
    # run: every cap is an independent counter, never a shared budget.
    for forms_bucket, max_per_run, bucket_label in (
        (config.EDGAR_ANNUAL_FORMS, config.EDGAR_MAX_ANNUAL_FILINGS_PER_RUN, "annual"),
        (config.EDGAR_QUARTERLY_FORMS, config.EDGAR_MAX_QUARTERLY_FILINGS_PER_RUN, "quarterly"),
    ):
        matched = 0
        items_added = 0
        for i in range(len(forms)):
            if matched >= max_per_run:
                logger.info("%s: reached EDGAR %s-filing cap (%d) — older filings left for a later run.",
                            ticker, bucket_label, max_per_run)
                break
            form = forms[i]
            if form not in forms_bucket:
                continue
            filed = filing_dates[i] if i < len(filing_dates) else None
            if filed is None:
                continue
            if last_scan is not None and filed <= last_scan:
                continue  # already seen in a previous scan

            accession = accession_numbers[i] if i < len(accession_numbers) else ""
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            desc = primary_descs[i] if i < len(primary_descs) else ""
            acc_nodash = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary_doc}"
                if primary_doc else
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
            )

            matched += 1
            new_items = _collect_periodic_filing_items(ticker, stock, cik, form, filed, filing_url, primary_doc, desc)
            items_added += len(new_items)
            form_counts[form] = form_counts.get(form, 0) + 1
            items.extend(new_items)

        if matched:
            logger.info("%s: EDGAR %s filings — %d filing(s) matched, %d classification item(s) produced.",
                        ticker, bucket_label, matched, items_added)

    # Note: this is filing counts per form (not item counts) — a single
    # 10-K/10-Q/DEF 14A/20-F filing can contribute several items (one per
    # resolved section), so `len(items)` below can exceed the sum of
    # `breakdown`'s filing counts.
    breakdown = ", ".join(f"{n} {f}" for f, n in sorted(form_counts.items())) or "none"
    logger.info("%s: EDGAR collected %d new classification item(s) from matched filings (%s) (since %s).",
                ticker, len(items), breakdown, last_scan or "beginning")
    record_feed_status(ticker, SOURCE_TYPE, "ok", feed_url=url)
    return items
