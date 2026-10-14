#!/usr/bin/env python3
"""Headless Sub-Zero cloud login with email + password (no browser).

Calls ``SZGCloudAuth.login_with_password``, which drives the Azure AD B2C
hosted sign-in page over plain HTTP and exchanges the resulting code for
tokens. Use ``examples/cloud_login.py`` instead if this reports that an
interactive login is required (for example MFA).

Usage:
    python3 examples/cloud_login_password.py [tokens_file]

    Default tokens file is ``cloud_tokens.json`` in the current directory.
    Credentials come from the ``SZG_EMAIL`` / ``SZG_PASSWORD`` environment
    variables, otherwise you are prompted (the password is not echoed).

WARNINGS:
    * Make ONE attempt. Repeated wrong-password attempts can lock the
      account. If credentials are rejected, fix them before trying again.
    * Try an account that is NOT MFA-enrolled first; MFA accounts need the
      browser flow.

The script never prints the password, tokens or the authorization code.
"""

from __future__ import annotations

import getpass
import os
import sys

from pyszg import (
    AuthenticationError,
    InteractiveLoginRequired,
    SZGCloudAuth,
    SZGError,
)


def main() -> int:
    tokens_file = sys.argv[1] if len(sys.argv) > 1 else "cloud_tokens.json"
    email = os.environ.get("SZG_EMAIL") or input("Email: ").strip()
    password = os.environ.get("SZG_PASSWORD") or getpass.getpass("Password: ")

    auth = SZGCloudAuth()
    try:
        tokens = auth.login_with_password(email, password)
    except InteractiveLoginRequired:
        # Must be caught before AuthenticationError (it is a subclass).
        print("Sub-Zero needs an interactive sign-in for this account (MFA, consent")
        print("page or a changed sign-in page). Use the browser flow instead:")
        print("  python3 examples/cloud_login.py")
        return 2
    except AuthenticationError as exc:
        print(f"Credentials rejected: {exc}")
        print("Do not retry blindly: repeated failures can lock the account.")
        return 1
    except SZGError as exc:
        print(f"Login failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        del password

    auth.save_tokens(tokens, tokens_file)
    print(f"Saved tokens for {tokens.email or tokens.user_id} to {tokens_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
