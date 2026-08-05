# KOSPI Shadow Decision Coach v5.2

기존 누수 통제형 KOSPI 예측 파이프라인에 **시간대별 시장 코칭 PWA**를 결합한 개인 연구용 앱입니다.

## v5.2 decision-first PWA

- 첫 화면을 KOSPI Gate의 **현재 행동·핵심 근거·판단 변경 조건·다음 체크포인트** 중심으로 압축합니다.
- 화면을 `오늘 판단`, `종목 후보`, `근거·연구` 3개 탭으로 분리하고 Model Lab·예측 원장·Data Lab·운영 상태는 기본 접힘으로 둡니다.
- KOSPI Gate 또는 종목 모델 승격 게이트가 진입을 허용하지 않으면 모든 종목 카드를 관찰 전용으로 잠그고 상세 조건을 기본 접습니다.
- `signal_enabled=false`인 확률은 연구값·매매 판단 미사용으로 표시해 숫자의 과대해석을 방지합니다.
- 새 서비스워커가 앱 화면 버전을 교체하면 열린 PWA도 새 셸로 이동하며, `앱 다시 받기`는 실제 서비스워커 검사와 페이지 재로딩을 수행합니다.
- 데이터는 여전히 체크포인트별 정적 스냅샷이며 실시간 시세·자동주문으로 표현하지 않습니다.

## v5.1 KOSPI Market Gate

- 앱 최상단의 **오늘 KOSPI 매매 판단**에서 `TRADE_OK`, `SELECTIVE`, `WAIT`, `RISK_OFF`, `UNAVAILABLE`를 구분합니다.
- 07:30, 08:00, 08:50, 09:05 체크포인트를 실제 Coach 배포 시각과 맞춥니다.
- 기존 KOSPI `intraday_up` 모델 확률은 **당일 시가→종가 상승 확률**로만 표시합니다. 별도 잔여장 모델이 없어 **현재 시점→종가 상승 확률**은 `null/unavailable`이며 기존 확률로 대체하지 않습니다.
- KIS KOSPI 상승·하락 종목 수로 시장 폭을 표시합니다. 대형주 편중은 구성종목 가중치 직접 측정값이 아니라 지수 등락과 시장 폭 괴리의 추정치로 명시합니다.
- `signal_enabled=false`이면 `TRADE_OK`를 절대 만들지 않으며 매매 보류(abstention)를 활성화합니다.
- 종목 신호는 KOSPI Market Gate가 `TRADE_OK` 또는 `SELECTIVE`이고 종목 모델 자체도 학습·승격됐을 때만 열립니다. 현재 종목 모델은 미학습 상태라 계속 fail-closed입니다.
- KOSPI Model Lab은 승격 기준과 검증 지표를 표시하고, 라이브 예측 원장은 durable `premarket-history` 브랜치의 `history/kospi/live_prediction_ledger.jsonl`에 체크포인트별 스냅샷을 보존합니다.

## v5 시간대별 투자 의사결정 코치

- Asia/Seoul 기준 아침 브리핑, NXT 프리마켓, 동시호가, 시초 확인, 09:05 진입 조건 확인, 장중 관리, 마감, NXT 애프터마켓, 20:05 익일 관찰 단계를 분리합니다.
- 설정된 `PREMARKET_SYMBOLS`만 실제 KIS `NX`/`J` 데이터로 관찰 순위를 만듭니다. 관찰 점수는 데이터 완전성과 방향성 참고 신호를 정렬하는 실험값이며 확률이 아닙니다.
- 종목 모델은 미학습 상태입니다. `signal_enabled=false`, 확률 `null`, `UnavailablePredictor`를 유지하고 관찰·대기·데이터 부족만 표시합니다.
- Google News RSS의 실제 시각을 KST로 정규화하고 같은 사건을 묶습니다. 날짜만 있는 기사는 임의 시각을 만들지 않습니다. `DART_API_KEY`가 있으면 설정 종목의 OpenDART 공식 공시를 선택적으로 수집합니다.
- KIS NXT 애프터마켓 시세는 15:40 이후 실제 수신에 성공한 경우에만 표시합니다. 동일 시간대 이력이 부족하면 상대거래량·상대거래대금은 산출하지 않습니다.
- Data Lab은 durable `premarket-history` 기록의 실제 표본·라벨·누락률만 집계합니다. Shadow 기록은 의사결정 스냅샷만 저장하며 조건을 충족하지 않은 가상 거래를 만들지 않습니다.
- 정적 PWA의 데이터 확인과 앱 화면 재수신을 구분합니다. 브라우저는 KIS·DART API를 직접 호출하지 않습니다.

구조와 운영 진실성은 [DECISION_COACH_V5.md](DECISION_COACH_V5.md)에 정리했습니다.

## v4.3 개별 종목 NXT 2단계 프레임워크

**2단계 데이터 수집·피처·검증 프레임워크. 종목 확률 모델은 미학습 상태.**

- 기존 KOSPI 지수 일간 예측을 그대로 유지하고, 개별 종목 기능은 별도 `premarket_experiment` API 영역으로 분리했습니다.
- KIS REST의 `NX` 시장 현재가·호가·예상체결·당일 분봉을 설정된 종목에만 수집합니다. 기본 종목 목록은 비어 있으며 저장소 변수 `PREMARKET_SYMBOLS` 또는 `premarket.symbols`로 명시해야 합니다.
- 장전 예측과 09:05 업데이트는 서로 다른 객체로 보존합니다. 09:05 전에는 첫 5분 피처를 사용하거나 업데이트 결과를 만들지 않습니다.
- 종목별 원시 스냅샷과 날짜·단계별 학습 레코드는 일반 시장 캐시와 분리합니다. Actions에서는 전용 `premarket-history` 브랜치에 직렬화해 보존하며, 최근 20거래일의 현재 시각 이하 가장 가까운 관측이 채워지기 전에는 상대거래량·상대거래대금을 계산하지 않습니다.
- 09:05 수집 결과는 같은 시각 Coach 빌드에서 Market Gate와 함께 PWA에 반영됩니다. Collector와 Coach는 동일한 직렬화 그룹을 사용합니다.
- 종목 학습·보정 데이터가 아직 없으므로 운영 응답의 세 확률은 `null`입니다. 임의 휴리스틱 확률은 사용하지 않습니다.
- 자세한 구조, 라벨, 공급자 경로와 데이터 가용성은 [TWO_STAGE_PREMARKET.md](TWO_STAGE_PREMARKET.md)에 정리했습니다.

## v4.2 확률 설명

- 최종 확률을 **학습 기준확률 + 원모델 확률 + 반영비중**으로 분해합니다.
- 상승·하락 기여 요인을 각각 최대 3개 표시합니다.
- 기여도는 각 변수를 학습 중간값으로 바꿔 본 국소 민감도이며 인과관계가 아닙니다.
- 원모델 가중치가 거의 0이면, 낮은 확률의 주된 이유가 과거 장중 상승 빈도임을 명시합니다.

## 앱 기능

- 07:30 장전, 08:00 NXT 확인, 08:50 선물·동시호가, 09:05 본장 첫 5분, 12:00, 15:20, 20:05 자동 업데이트
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
