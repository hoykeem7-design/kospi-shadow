# KOSPI Shadow Coach v4.1

기존 누수 통제형 KOSPI 예측 파이프라인에 **시간대별 시장 코칭 PWA**를 결합한 개인 연구용 앱입니다.

## 앱 기능

- 07:45 장전, 08:10 NXT 확인, 08:47 선물, 09:10 본장, 12:00, 15:20, 20:05 자동 업데이트
- KOSPI 확률, KIS KOSPI/KOSPI200 선물, 미국 지수·VIX·환율·금리, 뉴스·발표 캘린더 통합
- 모델 승격 기준이 닫혀 있으면 매수 코칭을 자동 차단
- PWA 설치 지원. `ENABLE_GITHUB_PAGES=true` 저장소 변수를 설정하면 Pages 자동 배포

> 앱의 타이밍 코칭은 실시간 확인 레이어이며, 기존 일간 모델 자체가 분 단위 최적 진입시각을 학습한 것은 아닙니다. 자동주문 기능은 없습니다.

---

# KOSPI SHADOW AUTO v3.2

Research-only KOSPI pre-open forecasting pipeline. It fails closed: probabilities may be produced, but `signal_enabled` stays false unless every promotion check passes.

## v3.2 fixes

- Fixes Yahoo batch factors failing with `Date is both an index level and a column label`.
- Rechecks the most recent five KRX business dates so a session queried before publication is not permanently skipped.
- Keeps the fast cached daily-prediction design introduced in v3.

## v3.2 KIS provisional close fallback

- Daily prediction keeps KRX as the official history.
- When the newest KRX daily row is delayed, the KIS domestic-index daily endpoint may append newer KOSPI rows as `kis_provisional`.
- Same-day KIS rows are accepted only after 15:45 Asia/Seoul to avoid ingesting an incomplete trading session.
- Weekly/full retraining excludes provisional KIS rows; only daily prediction may use them.
- A provisional latest row forces `target_official=false`, so the production signal remains disabled until KRX publishes the official row.

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
- **KRX Secret Smoke Test**: manual KRX authentication check.
- **KIS Index Smoke Test**: manual KIS token and KOSPI daily-index check.

GitHub repository secrets:

- `KRX_AUTH_KEY`
- `FRED_API_KEY`
- `KIS_APP_KEY`
- `KIS_APP_SECRET`

## Outputs

- `daily_brief.md` — easiest human-readable result
- `latest_prediction.json`
- `metrics.json`
- `model_card.md`
- full runs additionally produce `oos_predictions.csv`, `feature_tail.csv`, and the model file

## Interpretation

`LONG`, `FLAT`, and `SHORT` are research labels, not orders. The system does not model a tradable ETF/futures order book, taxes, spread, slippage, or guaranteed scheduled execution. Do not trade solely from this output.
