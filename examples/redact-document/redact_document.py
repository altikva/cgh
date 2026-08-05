# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Produce an anonymized copy of text: keep only the PII
#              categories you choose (here, names), replace each with a
#              stable token, and the same value always maps to the same
#              token. Names need the NER tier (cgh-pii[ner]); the regex
#              categories (email, iban, ...) work with cgh-pii alone.
#              Requires: pip install cgh cgh-pii   (and cgh-pii[ner] for names)

from __future__ import annotations

from codegraph import sdk

DOCUMENT = """Meeting notes, 2026-08-05.
Present: Jeanne Martin, Paul Dupont. Jeanne Martin chaired.
Follow up with jeanne.martin@example.com about invoice FR7630006000011234567890189.
"""


def main() -> None:
    # Anonymize only names, as numbered placeholders. Same name, same tag.
    try:
        only_names = sdk.redact_text(DOCUMENT, only=["person"])
        print("names only:\n" + only_names)
    except Exception as exc:  # RedactError when the NER extra is absent
        print(f"names need cgh-pii[ner]: {exc}\n")

    # Everything the regex tier finds (no NER needed): email, iban, ...
    no_contact = sdk.redact_text(DOCUMENT, only=["email", "iban"])
    print("\ncontact details:\n" + no_contact)

    # Keyed pseudonyms instead of placeholders: the same value maps to
    # the same token across documents when you reuse the secret.
    import secrets

    key = secrets.token_bytes(32)  # persist this to correlate across files
    pseudo = sdk.redact_text(DOCUMENT, only=["email"], mode="pseudonym", secret=key)
    print("\npseudonymized email:\n" + pseudo)


if __name__ == "__main__":
    main()
