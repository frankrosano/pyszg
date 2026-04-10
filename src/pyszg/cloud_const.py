"""Cloud API constants for Sub-Zero Group.

These values are extracted from the public Sub-Zero Owner's App
and are required for cloud API communication. They are not user secrets.
"""

API_BASE = "https://prod.iot.subzero.com"
SUBSCRIPTION_KEY = "e88bf0b60baf441583f822fa9ba9c895"

B2C_TENANT = "SubZeroB2CPrd.onmicrosoft.com"
B2C_HOST = "login.subzero-wolf.com"
B2C_POLICY = "B2C_1A_SIGNUP_SIGNIN"
CLIENT_ID = "6eefabd0-49a3-4b92-b329-81b9f638e940"
REDIRECT_URI = "msauth.com.subzero.group.owners.app://auth"
AUTHORIZE_URL = f"https://{B2C_HOST}/{B2C_TENANT}/{B2C_POLICY}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://{B2C_HOST}/{B2C_TENANT}/{B2C_POLICY}/oauth2/v2.0/token"
SCOPES = "openid offline_access"
