# Two-stage NXT premarket framework

## Scope and architecture

The production KOSPI path is unchanged: it predicts the next KOSPI index session's `intraday_up` label (`Close > Open`) with Logistic Regression and HistGradientBoosting candidates. Daily KOSPI, global daily factors, rates, and their leakage-controlled lags remain the inputs. Inner time-series validation selects a 0–100% blend of the raw model probability toward the training prior by Brier score; no separate Platt, isotonic, or beta calibrator is fitted.

The new path is an isolated individual-stock experiment: **2단계 데이터 수집·피처·검증 프레임워크. 종목 확률 모델은 미학습 상태.**

1. `premarket_data.py` calls point-in-time KIS REST endpoints and normalizes timestamps/quality.
2. Each configured symbol stores raw snapshots under `history/raw/` and one normalized record per date/stage under `history/training/`.
3. `premarket.py` calculates phase-safe features and labels without replacing missing values with zero.
4. `UnavailablePredictor` implements the two-stage predictor interface until labeled stock history is sufficient.
5. `premarket_backtest.py` keeps premarket and 09:05 evaluation separate.
6. `coach.py` adds the optional `premarket_experiment` response while retaining the old response fields.
7. The PWA renders the premarket and 09:05 objects side by side.

`NXT Premarket Snapshot Collector` runs a lightweight scheduled collection path. Collector and Coach use the shared `kospi-shadow-live-data` concurrency group and persist stock history to the dedicated `premarket-history` branch, not `actions/cache`. The general `data/cache` remains separate. This branch does not deploy or merge.

## Labels

- `open_to_0930_up`: true only when the observed 09:30 price is strictly above the actual open; missing when either observation is absent.
- `open_to_close_up`: true only when the official close is strictly above the actual open; missing when either observation is absent.
- `gap_direction`: `up`, `down`, or `flat` when the absolute opening gap is no greater than `minimum_gap_price_unit`; missing when inputs are absent.
- `gap_continuation_0930`: for an up gap, 09:30 must be above the open and extend the gap from the previous close; the inverse applies to a down gap. A flat/missing gap is N/A, not false.

The premarket stage accepts observations strictly before 09:00. Its predictor bundle contains `premarket_summary` and auction values observed by that cutoff. The update stage accepts observations through 09:05 inclusive and combines `premarket_summary`, `opening_auction_summary`, the complete `opening_five_minute_summary`, and market indicators received by that cutoff. The same validator is called when production bundles and backtest datasets are built. Missing `observed_at` is marked `unknown_time`; future features are excluded from production bundles and rejected by backtests. The framework never evaluates premarket performance with first-five-minute features.

## Probability and calibration truth

There is no stock-level trained model, calibrated probability map, or completed stock backtest in this repository. Consequently all three production stock probabilities are `null`, `probability_available=false`, `confidence=low`, and `experimental=true`. Model contribution values are also `null`; displayed factors are directional reference signals, not causal explanations or trading rules.

Training can only begin after enough point-in-time history and labels have accumulated. Any future implementation must use chronological symbol-sorted splits, separate calibration data, walk-forward evaluation, and stage-specific cutoffs. The supplied backtest interface reports Brier score, log loss, ROC-AUC, precision, recall, F1, calibration bins/ECE, cost-adjusted expected return, and maximum drawdown when sufficiently many labeled probabilities exist.

## Data availability

“Accumulated only” means KIS offers the point-in-time/current-day path, but this repository must collect and retain observations itself; it does not synthesize or backfill missing intraday history. No row below uses a fallback from another market, stock, session, or daily volume.

| Variable | Actually collectible | Provider | API/data path | Resolution | History now | Implementation | Live connected | Fallback | Missing behavior | UI |
|---|---|---|---|---|---|---|---|---|---|---|
| NXT premarket return | Yes, configured NXT-eligible stock | KIS | `inquire-price`, market `NX` | Snapshot | Accumulated only | Implemented | Yes when configured/credentialed | None | `null/unavailable` | Data not received |
| NXT high/low | Yes | KIS | `inquire-price`, market `NX` | Snapshot/session-to-time | Accumulated only | Implemented | Yes | None | `null` | Data not received |
| NXT cumulative volume | Yes | KIS | `inquire-price`, market `NX` | Snapshot/session-to-time | Accumulated only | Implemented | Yes | None | `null` | Data not received |
| NXT cumulative turnover | Yes | KIS | `inquire-price`, market `NX` | Snapshot/session-to-time | Accumulated only | Implemented | Yes | None | `null` | Data not received |
| Same-time relative volume | Derived after 20 sessions | Durable point-in-time store | `premarket-history:history/raw/<symbol>.jsonl` | Closest observation at or before the same minute | Starts after activation | Median implementation | Becomes live after 20 valid dates | None | `relative_value=null` | Baseline data insufficient |
| Same-time relative turnover | Derived after 20 sessions | Durable point-in-time store | Same as above | Closest observation at or before the same minute | Starts after activation | Median implementation | Becomes live after 20 valid dates | None | `relative_value=null` | Baseline data insufficient |
| Bid/ask spread | Yes | KIS | `inquire-asking-price-exp-ccn`, market `NX` | Snapshot | Accumulated only | Implemented | Yes | None | `null` | Data not received |
| Order-book quantity imbalance | Yes at best level | KIS | Same endpoint, best ask/bid quantities | Snapshot | Accumulated only | Implemented | Yes | None | `null` | Data not received |
| Trade strength | WebSocket provides it; REST snapshot may omit it | KIS | `H0NXCNT0` (`CTTR`) | Tick | Not retained before activation | Schema field present | Only if returned by REST; WebSocket collector not implemented | None | `null` | Data unavailable |
| Execution imbalance | WebSocket provides buy/sell execution counts | KIS | `H0NXCNT0` | Tick | Not retained before activation | Derivation present when counts exist | REST-dependent; continuous WebSocket not implemented | None | `null` | Data unavailable |
| News | Broad KOSPI news exists, stock material feed not connected | Google News RSS | Existing broad query | Article | No stock history | Stock material schema only | No | None | `unavailable` (not negative) | Data unavailable |
| Official disclosure | Technically available from a separate disclosure provider, not configured here | Not connected | No repository path | Event | None | Schema only | No | None | `unavailable` | Data unavailable |
| Expected execution price | Yes during available auction window | KIS | `inquire-asking-price-exp-ccn`, market `J`, `antc_cnpr` | Snapshot | Accumulated only | Implemented | Yes at scheduled observations | None | `null` | Auction data not received |
| Expected execution volume | Yes during available auction window | KIS | Same endpoint, `antc_vol` | Snapshot | Accumulated only | Implemented | Yes at scheduled observations | None | `null` | Auction data not received |
| Actual open | Yes after open | KIS | `inquire-price`/minute bars, market `J` | Snapshot/1 minute | Current day + accumulated store | Implemented | Yes | None | `null` | Data not received |
| First 1-minute return | Yes after complete bar | KIS | `inquire-time-itemchartprice`, market `J` | 1 minute | Current day; accumulated derived summary | Implemented | Yes | None | `null` | Collecting data |
| First 3-minute return | Yes after three complete bars | KIS | Same endpoint | 1 minute | Same | Implemented | Yes | None | `null` | Collecting data |
| First 5-minute return | Yes only after five complete bars | KIS | Same endpoint | 1 minute | Same | Implemented | Yes after 09:05 | None | `null`, no update prediction | Collecting data |
| First 5-minute volume | Yes only after five complete bars | KIS | Same endpoint | 1 minute | Same | Implemented | Yes after 09:05 | None | `null` | Collecting data |
| First 5-minute approximate VWAP | Derived from minute close × minute volume | KIS | Same endpoint | 1 minute | Same | Implemented as approximation | Yes when total volume > 0 | None | `null` when zero/missing volume | 근사 VWAP / data insufficient |
| Sector index | Provider integration not configured | Not connected | No repository path | — | None | API field reserved conceptually | No | None | `null/unavailable` | Data unavailable |
| Market advancer/decliner ratio | KIS KOSPI snapshot exposes counts | KIS | Existing domestic index snapshot | Snapshot | Not separately archived | Implemented when snapshot present | Yes in Coach generation | None | `null` | Data unavailable |

Other requested fields such as per-trade turnover concentration, exact aggressor-side execution imbalance, stock-specific official material classification, sector direction, and continuous order-book history need a continuous WebSocket/disclosure collection service. They are intentionally unavailable rather than inferred from unrelated data.

Scheduled auction observations are five minutes apart, so final-one-minute expected-price/volume fields remain `unavailable` and the UI says `미수집`. Opening hold uses minute-bar lows/highs, not closes alone. Recovery requires a later minute close back across the open; an intrabar breach-and-recovery order cannot be proven from OHLC and is documented as such.

## Time and quality

All phase decisions use `Asia/Seoul` explicitly:

- before 08:50: `premarket` / 프리장 예측
- 08:50–08:59: `opening_auction` / 동시호가 반영 중
- 09:00–09:04: `opening_confirmation` / 시초 확인 중
- from 09:05: `post_open_updated` / 확인 업데이트 완료

The Collector records the 09:05 snapshot but does not deploy the PWA. On the current schedule, the first production app build that can publish the completed 09:05 bundle is the **09:10 KOSPI Shadow Coach App run**. Therefore the PWA advertises 09:10, not collector-only 08:50/08:55/09:00/09:05 times, as an automatic app update.

Every provider snapshot carries `observed_at`, `received_at`, `data_delay_seconds`, `stale`, `data_quality`, and `source`. Missing provider timestamps remain unknown; stale state is controlled by `premarket.stale_after_seconds`.

## Configuration

`config/default.yml` exposes the baseline length, backward-only same-time tolerance, minimum samples, stale threshold, first confirmation time, gap label time/unit, liquidity floors, cost, slippage, outlier treatment, and history retention. Raw capacity is 25,000 records per symbol (more than five years at ten snapshots per trading day); normalized training capacity is 5,000 records (two stages per day). These are collection/validation settings, not fixed trading rules. Signal-strength thresholds are deliberately `null` until validation data supports them.

`NXT Stock Live Data Smoke Test` is manual and fails when no symbols are configured or no configured symbol returns available live KIS data. It records only configured/available counts; credentials are never printed.
