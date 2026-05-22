#!/usr/bin/env python3
"""Interactive Sub-Zero cloud login.

This is the application-level browser-and-paste flow that used to live
inside ``SZGCloudAuth.login()``. The library only exposes the building
blocks (``get_authorize_url`` and ``exchange_code``) so that production
consumers like the Home Assistant integration can drive their own flow
without prompting on stdin.

Usage:
    python examples/cloud_login.py [tokens_file]

    Default tokens file is ``cloud_tokens.json`` in the current directory.

The script:
    1. Generates a PKCE verifier/challenge pair.
    2. Opens the Sub-Zero login page in your default browser.
    3. Tells you to open developer tools (Console tab) and watch for the
       blocked navigation to ``msauth.com.subzero.group.owners.app://auth?code=...``.
       The redirect won't load in the browser — the URL only appears in
       the console.
    4. Waits for you to paste that URL into ``redirect_url.txt``.
    5. Exchanges the code for tokens and writes them to the tokens file.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from pyszg import SZGCloudAuth
from pyszg.cloud_const import REDIRECT_URI


def login_interactive(auth: SZGCloudAuth, redirect_url_file: str = "redirect_url.txt"):
    """Run the full interactive browser-paste flow against `auth`.

    Returns the resulting :class:`pyszg.TokenSet`.
    """
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    auth_url = auth.get_authorize_url(code_challenge, state)
    print("Opening browser for Sub-Zero login...")
    webbrowser.open(auth_url)

    print()
    print("Open developer tools in your browser (F12 or Cmd+Option+I) and switch")
    print("to the Console tab BEFORE clicking the login link. Sub-Zero's redirect")
    print(f"goes to a URL starting with:")
    print(f"  {REDIRECT_URI}?code=...")
    print("which browsers can't open. The blocked navigation logs in the Console")
    print("with the full URL — copy that and save it to:")
    print(f"  {redirect_url_file}")
    input("Press Enter once saved...")

    redirect_path = Path(redirect_url_file)
    if not redirect_path.exists():
        raise SystemExit(f"{redirect_url_file} not found")
    redirect_url = redirect_path.read_text().strip()

    if "?" not in redirect_url:
        raise SystemExit("No query string in redirect URL")
    qs = redirect_url.split("?", 1)[1]
    params = urllib.parse.parse_qs(qs)

    if "error" in params:
        raise SystemExit(params.get("error_description", params["error"])[0])
    if "code" not in params:
        raise SystemExit("No auth code in redirect URL")

    code = params["code"][0]
    return auth.exchange_code(code, code_verifier)


def main() -> None:
    tokens_file = sys.argv[1] if len(sys.argv) > 1 else "cloud_tokens.json"
    auth = SZGCloudAuth()
    tokens = login_interactive(auth)
    auth.save_tokens(tokens, tokens_file)
    print(f"Saved tokens for {tokens.email or tokens.user_id} to {tokens_file}")


if __name__ == "__main__":
    main()
