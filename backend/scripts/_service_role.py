"""Shared helper: switch the Supabase client to the service_role key.

Render only ever holds the anon (read-only) key — see the RLS section of
supabase_schema.sql ("write locally (service_role), read on Render (anon)").
Any script that
needs to write (refresh pipeline, one-off backfills) must run from a
trusted environment (local machine, GitHub Actions with the service_role
key in secrets) and call this before the first Supabase call.
"""

import os
import sys


def use_service_role_key() -> None:
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not service_key:
        print("SUPABASE_SERVICE_ROLE_KEY is not set — refusing to run.", file=sys.stderr)
        sys.exit(1)
    # get_supabase() (app.data.supabase_client) reads SUPABASE_KEY; overriding it
    # here — before any Supabase call is made — is what makes this process write
    # as service_role instead of whatever key the FastAPI app on Render uses.
    os.environ["SUPABASE_KEY"] = service_key
