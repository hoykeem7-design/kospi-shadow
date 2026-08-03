# Verification — KOSPI Shadow Coach v4.0

- Python compile: PASS
- pytest: 22 passed
- GitHub Actions YAML: 6 files parsed by PyYAML
- PWA JavaScript: `node --check` PASS for `app.js` and `sw.js`
- Mobile render smoke: PASS using Chromium at 390×844
- Secrets: application code only reads environment variables; static site output contains no API keys or secrets

## Safety properties

- A closed model promotion gate always returns `WAIT`.
- NXT pre-market does not recommend entry without a real-time NXT feed.
- The daily target remains the current session through 15:29 and rolls to the next business day after 15:30.
- The timing score is explicitly separate from the validated daily probability.
- No order endpoint or account credential is used.

## CI

The Test workflow runs pytest, PyYAML parsing, JavaScript syntax checks, and actionlint after push.
