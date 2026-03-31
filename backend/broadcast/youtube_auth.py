"""
YouTube Live Streaming API — OAuth2 credential management.

Requires GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET set in .env
(create OAuth2 credentials at https://console.cloud.google.com).

Setup:
  source .env
  cd /path/to/herakles-daimon/backend
  python -m broadcast.youtube_auth
  → Opens a URL — paste it in your browser, authorize, paste code back.
  → Saves refresh token to ~/.config/daimon/youtube_tokens.json
"""

import json
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger("broadcast.youtube_auth")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# OAuth2 credentials — create at https://console.cloud.google.com
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

TOKEN_PATH = os.environ.get(
    "YOUTUBE_TOKEN_PATH",
    "/secrets/youtube_tokens.json",  # mount your token file via docker-compose volumes
)


def get_credentials() -> Credentials | None:
    """Load or refresh YouTube API credentials."""
    creds = None

    if Path(TOKEN_PATH).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_tokens(creds)
            logger.info("YouTube tokens refreshed")
        except Exception as e:
            logger.error("YouTube token refresh FAILED — chat bridge will degrade to innertube: %s", e)
            creds = None

    if not creds or not creds.valid:
        return None

    return creds


def authorize_interactive(headless: bool = False) -> Credentials:
    """Run interactive OAuth2 flow using existing Google OAuth client."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set.\n"
            "Run: source .env"
        )

    # Build client config from env vars (no client_secret.json file needed)
    client_config = {
        "installed": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "http://localhost:9299"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    if headless:
        # Manual flow: print URL, user pastes the redirect URL back
        # Works on remote servers without SSH tunnel
        flow.redirect_uri = "http://localhost:9299"
        auth_url, _ = flow.authorization_url(prompt="consent")
        print(f"\n1. Open this URL in your browser:\n\n   {auth_url}\n")
        print("2. Authorize the app, then copy the FULL redirect URL from your browser")
        print("   (it will look like http://localhost:9299/?code=...&scope=...)\n")
        redirect_response = input("3. Paste the redirect URL here: ").strip()
        flow.fetch_token(authorization_response=redirect_response)
        creds = flow.credentials
    else:
        # Local server flow: opens browser, listens on port 9299
        # Requires browser access to localhost:9299 (SSH tunnel: -L 9299:localhost:9299)
        creds = flow.run_local_server(port=9299, open_browser=False)

    _save_tokens(creds)
    logger.info("YouTube authorization complete — tokens saved to %s", TOKEN_PATH)
    return creds


def _save_tokens(creds: Credentials) -> None:
    """Persist tokens to disk."""
    Path(TOKEN_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)


if __name__ == "__main__":
    import sys

    headless = "--headless" in sys.argv

    print("YouTube OAuth2 Setup for Daimon Live")
    print("=" * 40)
    print(f"Client ID: {GOOGLE_CLIENT_ID[:20]}..." if GOOGLE_CLIENT_ID else "Client ID: NOT SET")
    print(f"Token storage: {TOKEN_PATH}")
    print(f"Mode: {'headless (copy-paste)' if headless else 'local server (port 9299)'}")
    print()

    existing = get_credentials()
    if existing:
        print("Valid credentials already exist!")
    else:
        if not GOOGLE_CLIENT_ID:
            print("ERROR: Run 'source .env' first")
            exit(1)
        if not headless:
            print("Starting OAuth2 flow (local server on port 9299)...")
            print("If on a remote server, either:")
            print("  - SSH tunnel: ssh -L 9299:localhost:9299 server")
            print("  - Or use: python -m broadcast.youtube_auth --headless")
            print()
        authorize_interactive(headless=headless)
        print("Done! YouTube credentials saved.")
