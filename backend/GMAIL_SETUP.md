# Gmail newsletter collector — setup

The `newsletter` source of the qualitative layer reads emails from a curated
set of senders, **read-only**, to extract per-ticker events. It is **disabled
by default** (`SOURCE_FLAGS["newsletter"] = False` in
`app/qualitative/config.py`) and requires a one-time OAuth setup below. The
other three sources (EDGAR, IR RSS, press) need none of this.

## Scope

Only `https://www.googleapis.com/auth/gmail.readonly`. The collector never
sends, drafts, labels, or deletes anything.

## Steps

1. **Create OAuth credentials.**
   - Google Cloud Console → *APIs & Services* → enable the **Gmail API**.
   - *Credentials* → *Create credentials* → *OAuth client ID* → application
     type **Desktop app**.
   - Download the client-secret JSON. Save it as
     `backend/gmail_credentials.json` (gitignored) and point
     `GMAIL_API_CREDENTIALS_PATH` at it in `.env`.

2. **Run the one-time consent flow** (locally, once). This opens a browser,
   asks you to grant read-only access, and writes `gmail_token.json` next to
   the credentials file:

   ```python
   # backend/scripts/gmail_authorize.py  (run once, local machine)
   import os
   from pathlib import Path
   from google_auth_oauthlib.flow import InstalledAppFlow
   from app.qualitative.collectors.gmail_newsletter import GMAIL_SCOPES

   creds_path = os.environ["GMAIL_API_CREDENTIALS_PATH"]
   flow = InstalledAppFlow.from_client_secrets_file(creds_path, GMAIL_SCOPES)
   creds = flow.run_local_server(port=0)
   Path(creds_path).with_name("gmail_token.json").write_text(creds.to_json())
   print("gmail_token.json written.")
   ```

   The refresh job (headless) never runs an interactive flow — it only uses the
   already-written `gmail_token.json` and refreshes it silently when expired.

3. **Curate the sender list.** Copy `newsletter_senders.example.json` to the
   path in `NEWSLETTER_SENDERS_FILE` (default
   `qualitative_cache/newsletter_senders.json`) and fill in the `senders`
   array. An empty/absent file means no newsletters are scanned.

4. **Enable the source.** Set `SOURCE_FLAGS["newsletter"] = True` in
   `app/qualitative/config.py`.

## Notes

- `gmail_credentials.json` and `gmail_token.json` are gitignored — never commit
  them.
- For an unattended (GitHub Actions) run, the token must be provisioned into
  the runner (e.g. via a secret) — out of scope here; the collector cleanly
  records `feed_status = failed` if no valid token is present, rather than
  crashing the run.
