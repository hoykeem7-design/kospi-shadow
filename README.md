# KOSPI Shadow Coach v4.3

기존 누수 통제형 KOSPI 예측 파이프라인에 **시간대별 시장 코칭 PWA**를 결합한 개인 연구용 앱입니다.

## v4.3 개별 종목 NXT 2단계 실험

- 기존 KOSPI 지수 일간 예측을 그대로 유지하고, 개별 종목 기능은 별도 `premarket_experiment` API 영역으로 분리했습니다.
- KIS REST의 `NX` 시장 현재가·호가·예상체결·당일 분봉을 설정된 종목에만 수집합니다. 기본 종목 목록은 비어 있으며 저장소 변수 `PREMARKET_SYMBOLS` 또는 `premarket.symbols`로 명시해야 합니다.
- 장전 예측과 09:05 업데이트는 서로 다른 객체로 보존합니다. 09:05 전에는 첫 5분 피처를 사용하거나 업데이트 결과를 만들지 않습니다.
- 종목별 시점 스냅샷은 `data/cache/premarket/<symbol>.jsonl`에 누적합니다. 최근 20거래일 동일 시간대 표본이 채워지기 전에는 상대거래량·상대거래대금을 계산하지 않습니다.
- 종목 학습·보정 데이터가 아직 없으므로 운영 응답의 세 확률은 `null`입니다. 임의 휴리스틱 확률은 사용하지 않습니다.
- 자세한 구조, 라벨, 공급자 경로와 데이터 가용성은 [TWO_STAGE_PREMARKET.md](TWO_STAGE_PREMARKET.md)에 정리했습니다.

## v4.2 확률 설명

- 최종 확률을 **학습 기준확률 + 원모델 확률 + 반영비중**으로 분해합니다.
- 상승·하락 기여 요인을 각각 최대 3개 표시합니다.
- 기여도는 각 변수를 학습 중간값으로 바꿔 본 국소 민감도이며 인과관계가 아닙니다.
- 원모델 가중치가 거의 0이면, 낮은 확률의 주된 이유가 과거 장중 상승 빈도임을 명시합니다.

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
