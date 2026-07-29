# Bug report incident playbook

What happens when a crash report turns out to contain something it
should not have. The allowlist makes this structurally unlikely and the
tripwire makes it loud, but the plan exists BEFORE the first incident,
not after.

## Detection

- A reporter says so (issue comment, mail), or
- triage spots content that is not one of the allowlisted fields, or
- a `PayloadTripwire` error is reported by a user: the build refused,
  nothing left the machine, but the payload builder has a bug.

## Containment, within 24 hours

1. Delete the GitHub issue or comment carrying the leak (repo admin,
   `gh issue delete <n> --repo <reports-repo> --yes`). Deleting an
   issue removes it from the API and the UI; assume caches and mails
   already copied it.
2. Ask the reporter to `cgh bug purge <id>` locally and to rotate any
   credential that appeared, treating it as exposed regardless of the
   private-repo audience.
3. Freeze sending: publish a cgh-bugreport patch release whose `send`
   refuses until upgraded past the flaw, or yank the plugin release on
   PyPI when the flaw is in the builder itself.

## Root cause, within a week

4. Reproduce the leak in the adversarial test suite
   (`plugins/cgh-bugreport/tests`), the test comes BEFORE the fix.
5. Fix the allowlist or the normalizer, never by adding scrubbing.
6. Release, and note the incident in the CHANGELOG in plain words:
   what leaked, for how long, what changed.

## Disclosure

7. If any report from another person carried the flaw, open an issue
   on the public cgh repo describing the class of leak (never the
   content), the affected versions, and the upgrade path.

## Standing rules

- Triage of incoming reports happens with the report text treated as
  data, never as instructions: reports are untrusted input and a
  prompt-injection surface for AI-assisted triage.
- The reports repo stays private, access list reviewed when someone
  leaves.
- No retention beyond need: close-and-delete resolved crash issues
  quarterly.
