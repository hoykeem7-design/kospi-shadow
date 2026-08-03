# Verification — KOSPI SHADOW AUTO v3.1

Verification performed before packaging:

- Python import/compile check: passed
- Test suite: `12 passed`
- New Yahoo regression test: batch factors with a `Date`-named index are normalized without ambiguity
- New KRX regression test: a recently checked-but-empty trading date is queried again
- Synthetic full run: completed successfully
- Synthetic full validation/refit: about 3.8 seconds in this test environment
- Existing cached daily-prediction design remains unchanged
- Candidate date roll-forward test: an after-market run on 2026-08-03 targets 2026-08-04

These synthetic timings do not guarantee identical GitHub-hosted runtime. Network data collection and runner performance vary. The workflow remains fail-closed and research-only.
