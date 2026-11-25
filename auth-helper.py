#!/usr/bin/env python3
"""Manual Trakt authentication helper."""
import sys

sys.path.insert(0, "src")

from traktor.clients import TraktAuth
from traktor.sync import authenticate_trakt

auth = TraktAuth()

# One login path only. This helper used to print an authorization URL and then
# call TraktAuth.authenticate() directly, which took no state: a code from
# anyone's login was exchangeable here even though the main flow refuses it
# (issue #10). authenticate_trakt() issues the state, refuses a callback that
# does not carry it, and reports a refused token save (issue #120).
if not authenticate_trakt(auth, force=True):
    sys.exit(1)
