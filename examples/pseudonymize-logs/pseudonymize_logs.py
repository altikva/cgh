# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Log user activity without ever writing the sensitive
#              value: your pipeline pseudonymizes the fields it holds
#              with a keyed one-way pseudonym. Stable (same value, same
#              pseudonym, so the two events by the same user correlate)
#              and irreversible (HMAC does not decode). The scan is the
#              tripwire: it proves the safe line carries no PII before
#              it reaches the log. Requires: pip install cgh cgh-pii

from __future__ import annotations

import secrets

from codegraph import sdk

SECRET = secrets.token_bytes(32)  # persist this once in your app's vault

EVENTS = [
    {"user_email": "jeanne.martin@example.com", "action": "opened ticket 4812"},
    {"user_email": "paul.dupont@example.com", "action": "escalated ticket 4812"},
    {"user_email": "jeanne.martin@example.com", "action": "closed ticket 4812"},
]


def main() -> None:
    for event in EVENTS:
        who = sdk.pseudonymize("pii.email", event["user_email"], SECRET)
        line = f"{who} {event['action']}"

        # Tripwire: the line about to be logged must scan clean.
        leaked = [
            f for f in sdk.scan_text(line, scanners=["pii"]) if f.key.startswith("pii.")
        ]
        assert not leaked, f"PII survived pseudonymization: {leaked}"
        print(line)
    # The first and third line share a pseudonym: same user, still
    # correlatable, address nowhere.


if __name__ == "__main__":
    main()
