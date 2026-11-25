#!/usr/bin/env python3
"""One-time sign-in as the omnibot Google user, to make the Jenkins credential file.

  pip install google-auth-oauthlib
  python tools/google_login.py --client client_secret.json --out omnibot-google.json

--client is an OAuth client of type "Desktop app" (Google Cloud console → APIs &
Services → Credentials). A browser opens: sign in as omnibot and allow access to
Google Sheets. The output file holds a refresh token and is what
GOOGLE_APPLICATION_CREDENTIALS points at; upload it to Jenkins as the secret file
`omnibot-google-sa`, then delete the local copy.

Use an OAuth consent screen with user type "Internal" (Workspace) or a published
"External" app. In "Testing" mode Google expires refresh tokens after 7 days and the
job would stop publishing.
"""
import argparse
import json
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True, help="OAuth client JSON (Desktop app) downloaded from Google Cloud")
    ap.add_argument("--out", default="omnibot-google.json")
    a = ap.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(a.client, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token; remove the app's access for omnibot and try again")
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(json.loads(creds.to_json()) | {"type": "authorized_user"}, f, indent=2)
    print("Wrote {} (signed in; keep it secret). Upload it to Jenkins as secret file "
          "'omnibot-google-sa', then delete this copy.".format(a.out))


if __name__ == "__main__":
    main()
