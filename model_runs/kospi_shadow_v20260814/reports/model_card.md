# KOSPI SHADOW COACH v4.0 — Model Card

## Status

**RESTRICTED_SHADOW**  
`signal_enabled=false`

## Current research prediction

- Candidate target date: 2026-08-17
- Intraday-up probability: 0.4140
- Research direction: SHORT
- Timing valid: False
- Actionable: **false** — Model promotion gate is closed; research output only.

## Validation

- OOS observations: 3586
- Model Brier: 0.248453
- Expanding-prior baseline Brier: 0.249868
- Brier improvement: 0.001416
- Bootstrap probability of beating baseline: 0.998
- Probability predictions are shrunk toward the training prior using inner time-series CV.

## Data

- Target provider: `krx_official_open_api`
- Official target: `True`
- Target range: 2010-01-04 to 2026-08-13
- Factors: nasdaq, sox, sp500, us10y, us2y, usdk_rw, vix
- Collection warnings: 0

## Operating design

- Weekly/full mode performs leakage-controlled validation and refits the model.
- Daily/predict mode reuses the validated state and only refreshes data and the next-session probability.
- This remains a research system and does not execute trades.
