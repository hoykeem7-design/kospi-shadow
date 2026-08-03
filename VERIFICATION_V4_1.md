# Verification — KOSPI Shadow Coach v4.1

- Automatic Netlify production deployment from GitHub Actions.
- Eight Korea-time market checkpoints including 15:35 post-close refresh.
- Live dashboard JSON is network-first and excluded from service-worker static cache.
- Refresh button reports whether a newer deployment exists instead of pretending to rerun the model.
- Runtime session badge adapts to the current Korea time even between scheduled deployments.
- Netlify credentials remain GitHub Actions secrets and are never written into the PWA bundle.
