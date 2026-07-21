"""V2 qualitative layer.

An AI-fed layer that scans news/filings sources for 8 categories of events
per ticker (dated_contract, m_and_a, regulatory_admission, guidance, backlog,
governance_risk, activist_pressure, customer_concentration), stores them in
``qualitative_notes`` and exposes a per-ticker event tally.

STRICTLY SEPARATE from the V1 scoring path: nothing here is ever injected
into composite_score / fundamental_score / risk_score. It is an additive,
display-only signal (a count of events, never a score), shown alongside the
quantitative scores, never fused arithmetically with them.
"""
