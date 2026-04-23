#!/usr/bin/env python3
"""Manual Trakt authentication helper."""
import sys
sys.path.insert(0, 'src')

from traktor.clients import TraktAuth

auth = TraktAuth()

print("\nTrakt Authentication")
print("===================")
print("\n1. Visit this URL in your browser:")
print(f"   {auth.get_auth_url()}")
print("\n2. Log in to Trakt and authorize the application")
print("3. Copy the authorization code from the page")
print()

auth_code = input("Authorization code: ").strip()

if not auth_code:
    print("Error: No code entered")
    sys.exit(1)

try:
    auth.authenticate(auth_code)
    print("\nSuccessfully authenticated with Trakt!")
    print("Tokens saved. You can now run traktor with watch sync.")
except Exception as e:
    print(f"\nError: {e}")
    sys.exit(1)
