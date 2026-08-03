# Verification — KOSPI SHADOW AUTO v3

Verification performed before packaging:

- Python compile check: passed
- Test suite: `10 passed`
- Synthetic full run: completed successfully
- Synthetic prediction-only run: completed successfully
- Synthetic benchmark with 900 sessions and 126-session outer blocks:
  - full validation/refit: about 5.3 seconds in the test environment
  - prediction-only: about 0.04 seconds
- Candidate date roll-forward test: an after-market run on 2026-08-03 targets 2026-08-04

These synthetic timings do not guarantee identical GitHub-hosted runtime. Network data collection and runner performance vary. The workflow remains fail-closed and research-only.
