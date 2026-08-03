# Verification Record

## Passed in this environment

- Python source compilation completed.
- Package wheel built successfully with the already-installed build toolchain.
- CLI entry point installed and `kospi-shadow --help` executed.
- **9 pytest tests passed**.
- KRX response-schema parsing tested with deterministic mocked responses.
- KRX `AUTH_KEY` header, date parameter, and incremental cache behavior tested with mocks.
- FRED missing-value parsing tested with mocks.
- Strict external-factor alignment tested: same-date factors are prohibited.
- KOSPI-derived feature lagging tested.
- Expanding walk-forward chronology tested: every recorded training end precedes its test date.
- Intraday transaction cost tested as two sides per non-zero daily position.
- Promotion gate tested both in passing and fail-closed conditions.
- End-to-end artifacts generated with deterministic synthetic data.
- Unofficial target source correctly forced `signal_enabled=false`.
- Latest prediction output correctly forced `actionable=false`.

## Not passed or not attempted

- **No live market retraining was performed.**
- Live KRX request was not attempted because no user KRX API key/service approval was available.
- Live FRED request was not attempted because no user FRED API key was available.
- Live Yahoo request was not attempted because outbound DNS/network is restricted in this execution sandbox.
- GitHub scheduled execution was not tested because the connected GitHub account exposed no accessible repository.
- Real ETF/futures execution, Korean exchange holiday verification, spread, taxes, market impact, and fill quality were not validated.

## Build-environment note

A normal isolated editable install first attempted to resolve build dependencies through the sandbox package index and failed because that index exposed no setuptools package. Installation and wheel construction then succeeded with `--no-build-isolation` using the installed setuptools 82.0.1. This is an execution-sandbox package-index limitation, not evidence that online GitHub installation has passed; the GitHub workflow must still be run once in an actual repository.
