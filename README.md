# KOSPI SHADOW AUTO v2 — Verified Build

This repository is a research-only KOSPI pre-open forecasting pipeline. It is designed to **fail closed**: a model may be trained and audited, but `signal_enabled` remains false unless every promotion requirement passes.

## What was corrected from the earlier proposal

- No claim that browser scraping is the most accurate or most reliable source.
- No claim that Yahoo Finance is official; it is fallback-only.
- No unnecessary XGBoost/LightGBM/CatBoost model zoo. More candidate models increase selection bias unless nested validation and multiple-testing controls are expanded.
- No arbitrary “4,000–8,000 lines” estimate.
- No claim of successful live retraining without current synchronized data and actual test logs.
- GitHub Actions is treated as best-effort scheduling, not an exact-time production scheduler.

## Data hierarchy

1. **KRX OPEN API** for the KOSPI target series. Requires `KRX_AUTH_KEY` and per-service approval.
2. **FRED API** for Treasury yields. Requires `FRED_API_KEY`.
3. **Yahoo via yfinance** for US-market factors and as an unofficial target fallback.

A Yahoo target fallback automatically fails the official-source promotion check.

## Leakage controls

- KOSPI technical predictors are shifted by at least one session.
- External factors use `merge_asof(..., allow_exact_matches=False)`: a factor dated `t` cannot be used for KOSPI date `t`.
- Outer evaluation is expanding walk-forward.
- Candidate selection occurs only inside each outer training window.
- Inner time splits include a purge gap.
- Baseline probability is calculated from each outer training window only.

## Run locally

```bash
export KRX_AUTH_KEY="..."   # optional, but required for official target status
export FRED_API_KEY="..."  # optional; missing FRED factors are logged
python -m pip install -e ".[test]"
pytest -q
kospi-shadow --config config/default.yml --project-root .
```

Outputs are written to `outputs/`:

- `metrics.json`
- `oos_predictions.csv`
- `model_card.md`
- `challenger_model.joblib`
- `data_manifest.json`
- `feature_columns.json`
- `latest_prediction.json`

## GitHub setup

Add repository secrets:

- `KRX_AUTH_KEY`
- `FRED_API_KEY`

Run **KRX Secret Smoke Test** manually first. After it passes, the daily workflow runs at 08:17 Asia/Seoul on weekdays and can also be run manually. GitHub scheduled workflows can be delayed; the workflow is therefore not suitable for guaranteed order-timing.

## Important limitation

The cost-adjusted strategy charges two transaction-cost sides per non-zero intraday position, but is still an **index-return proxy**, not a real ETF or futures execution backtest. The system should not be promoted to real-money use until a tradable instrument, spread, fees, taxes, slippage, and fill rules are modeled from point-in-time data.
