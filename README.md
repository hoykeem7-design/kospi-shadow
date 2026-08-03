# KOSPI SHADOW AUTO v3

Research-only KOSPI pre-open forecasting pipeline. It fails closed: probabilities may be produced, but `signal_enabled` stays false unless every promotion check passes.

## v3 upgrades

- **Daily prediction and weekly retraining are separated.** Daily runs reuse cached model state and should normally finish in minutes, not nearly an hour.
- KRX, Yahoo and FRED data are incrementally cached.
- Yahoo factors are downloaded in one batch. Factor features use Close only, fixing the false `KRW=X` OHLC consistency rejection.
- The next-session date rolls forward after 09:00, so an evening run no longer labels the already-finished session as the candidate target.
- Walk-forward validation uses a compact candidate set and quarterly test blocks to reduce runtime.
- Candidate probabilities are shrunk toward the training prior using inner time-series CV. This reduces overconfident weak signals and permits a prior-only result when models add no value.
- Progress and runtime timings are printed during data collection and validation.
- Every run creates `daily_brief.md` and publishes it in the GitHub job summary.

## Workflows

- **Daily KOSPI Shadow**: weekdays at 08:05 Asia/Seoul. Uses `--mode auto`; predicts from a model no older than eight days, or bootstraps a full train when state is missing.
- **Weekly KOSPI Shadow Retrain**: Saturday at 09:10 Asia/Seoul. Runs complete validation and refreshes model state.
- **KRX Secret Smoke Test**: manual authentication check.

GitHub repository secrets:

- `KRX_AUTH_KEY`
- `FRED_API_KEY`

## Outputs

- `daily_brief.md` — easiest human-readable result
- `latest_prediction.json`
- `metrics.json`
- `model_card.md`
- full runs additionally produce `oos_predictions.csv`, `feature_tail.csv`, and the model file

## Interpretation

`LONG`, `FLAT`, and `SHORT` are research labels, not orders. The system does not model a tradable ETF/futures order book, taxes, spread, slippage, or guaranteed scheduled execution. Do not trade solely from this output.
