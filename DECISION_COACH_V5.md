# Decision Coach v5.3.1 architecture and operating truth

## Theme & Supply Radar v5.3

`theme_radar.py`는 현재 체크포인트까지 수신된 기사·공시와 설정 종목의 NXT/첫 5분 수급만 사용해 테마 관찰 묶음을 만듭니다. 키워드는 테마 사건을 분류할 뿐 기업과 테마의 관계를 새로 만들지 않습니다. 구성 종목은 `PREMARKET_SYMBOLS`, 명시적인 테마 설정, 또는 시점이 검증된 기사·공시의 `related_symbols`에서만 가져옵니다.

테마별 출력은 기사·공시 빈도 프록시, 구성 종목 수, 상승 종목 비율, 상대거래대금·상대거래량 중앙값, 전일 NASDAQ/SOX 정렬, 전일 종가 기준, 날씨 데이터 가용성, 대장/후속 관찰 순서와 추격 위험을 분리합니다. 포털 조회수·검색순위는 연결되지 않았고 기사 빈도로 대체하지 않습니다. 직접 날씨 관측·예보가 없으면 날씨 기사는 `news_proxy`로만 표시합니다.

레이더는 `mode=shadow_only`, `entry_signal_enabled=false`, `probability=null`이며 KOSPI Gate를 우회하지 못합니다. 07:30·08:00·08:50·09:05 스냅샷은 `app_state/theme_supply_radar.jsonl`에 저장되며 5·15·30분 결과 필드는 검증 데이터가 생기기 전까지 `null`입니다.

### v5.3.1 market-attention coverage

KIS `volume-rank`에서 거래대금·거래량 증가 상위 KOSPI 종목 일부를 수신합니다. 이 값은 포털 조회수나 검색순위가 아닙니다. 명시적인 테마 종목표는 실제 수신된 순위 종목과 교차하고, Open-Meteo 서울 모델 예보는 날씨 사건 문맥으로만 사용합니다. 학습되지 않은 현재→종가·종목 확률은 휴리스틱으로 채우지 않고 계속 `null`로 둡니다.

## Decision-first PWA v5.2

운영 화면은 `오늘 판단`, `종목 후보`, `근거·연구`로 분리됩니다. 첫 화면은 KOSPI Market Gate의 현재 행동과 판단 근거, 다음 변경 조건, 체크포인트 시각을 우선합니다. 확률 설명, Model Lab, 라이브 예측 원장, Data Lab, 수집 상태는 연구 탭의 기본 접힘 영역에 유지하므로 데이터 계약과 감사 가능성을 삭제하지 않습니다.

KOSPI Gate의 `stock_entries_allowed` 또는 종목 `signal_gate.stock_signal_enabled`가 거짓이면 종목 카드는 관찰 전용으로 잠기며, 조건 상세는 사용자가 펼치기 전까지 숨깁니다. `signal_enabled=false`인 KOSPI 확률은 연구값으로만 표시합니다. PWA는 새 정적 캐시가 활성화될 때 기존 열린 화면을 다시 탐색하며, 사용자가 `앱 다시 받기`를 누르면 서비스워커 업데이트 검사 후 실제 페이지 재로딩을 수행합니다.

## KOSPI Market Gate v5.1

`market_gate.py` combines the existing KOSPI open-to-close probability with time-appropriate confirmation data. The gate emits exactly one of `TRADE_OK`, `SELECTIVE`, `WAIT`, `RISK_OFF`, or `UNAVAILABLE`. `TRADE_OK` requires `promotion.signal_enabled=true`, a same-day probability, and positive 09:05 spot, futures, and breadth confirmation. A disabled promotion gate can never emit `TRADE_OK`.

The existing `probability_intraday_up` target is KOSPI close above the same day's open. It is not a probability from the current clock time to the close. Until a separate remaining-session model is trained and validated, `current_to_close_up_probability` remains `null/unavailable`.

Market breadth uses KIS advancer and decliner counts. Large-cap concentration is only an explicitly labeled breadth/index-divergence proxy because direct constituent-weight contribution data is not connected. Gate snapshots are written to `history/kospi/live_prediction_ledger.jsonl` in the same durable history branch used by the decision coach.

## Existing architecture retained

The daily KOSPI index path remains the only trained prediction path. It builds leakage-controlled daily features from KRX/KIS provisional KOSPI history, Yahoo factors and FRED series, compares Logistic Regression and HistGradientBoosting candidates, and shrinks the selected probability toward the training prior using inner time-series validation. The original promotion gate and probability explanation remain unchanged.

The stock path remains separate. `premarket_data.py` collects configured stocks, `premarket.py` calculates point-in-time features and labels, and `UnavailablePredictor` returns null probabilities until stock-level labeled history, walk-forward validation and calibration exist. `decision_coach.py` consumes those real summaries to produce phase-aware observation and condition cards. It does not convert observation scores into probabilities or orders.

`coach.py` writes one backward-compatible static `site/data/dashboard.json`. The browser only reads this deployment. It does not call market APIs. Coach and Collector serialize writes to the durable `premarket-history` branch with a shared concurrency group.

## Phases and actual publication

| Phase | KST interval | Primary decision | Data checkpoint | Coach/Netlify publication |
|---|---|---|---|---|
| `overnight_brief` | before 08:00 | market environment and first watch | cached global factors, RSS/FRED | 07:30 |
| `nxt_premarket` | 08:00–08:50 | observation ranking | Coach 08:00; Collector 08:12/22/32/42 | 08:00 |
| `opening_auction` | 08:50–09:00 | maintained/strengthened/weakened/excluded | Collector/Coach 08:50; Collector 08:55 | 08:50 |
| `opening_confirmation` | 09:00–09:05 | collecting, no entry state | Collector 09:00/05 | 09:05 |
| `entry_decision` | 09:05–09:30 | conditions and invalidation | complete first five minutes | 09:05 |
| `intraday_management` | 09:30–15:30 | thesis/risk review | 09:30, 12:00, 15:20 | 12:00, 15:20 |
| `closing_review` | 15:30–15:40 | separate premarket/09:05 labels | KRX close | 15:35 |
| `nxt_aftermarket` | 15:40–20:05 | KRX-close gap and liquidity | Collector 15:42/17:00/19:00/20:00 | 15:45, 18:00, 20:05 |
| `next_day_watch` | from 20:05 | next-session recheck list | after-market final snapshot/news | 20:05 |

`scheduled_at` is the checkpoint start and `generated_at` is the actual build time. `schedule_delay_seconds` preserves lateness instead of pretending a late run was generated at its scheduled time.

## Data availability

| Item | Provider/path | Current live connection | Missing behavior |
|---|---|---|---|
| KOSPI index and KOSPI200 futures | KIS REST | yes when credentials work | null/unavailable; provider time may be `unknown_time` |
| Global indices, semiconductor, VIX, USD/KRW, US rates | Yahoo/FRED cache | existing daily path | omitted, never replaced with zero |
| NXT premarket price/volume/turnover/book | KIS REST market `NX` | configured symbols only | unavailable |
| Same-time relative NXT volume/turnover | durable raw history, median of prior dates at or before the same minute | after minimum valid history | null with baseline reason |
| KRX auction expected price/volume | KIS REST market `J` | scheduled snapshots | unavailable; final-one-minute field stays uncollected |
| KRX first 1/3/5-minute bars | KIS REST minute endpoint | after completed bars | collecting/unavailable before 09:05 |
| VWAP | minute close × minute volume | approximate only | null for zero/missing volume; UI says 근사 VWAP |
| Market breadth | KIS KOSPI snapshot | when counts are returned | unavailable |
| Sector index | no configured provider | no | unavailable |
| Google News article time | Google News RSS | broad query; symbol relation inferred only when configured name/code appears | unknown/date-only retained |
| Official disclosure | optional OpenDART list API | only with `DART_API_KEY` and configured symbols | `DART_API_KEY_NOT_CONFIGURED` or unavailable |
| NXT aftermarket price/volume/turnover/book | KIS REST market `NX`, after 15:40 | attempted for configured symbols | unavailable when no NXT response |
| Aftermarket relative metrics | durable raw history, backward-only same-time median | after minimum valid history | null, no daily-volume fallback |
| Tick aggressor, continuous book, sector breadth | no continuous collector | no | unavailable |

## News time and leakage

Timezone-aware article timestamps are converted to Asia/Seoul. Date-only disclosures remain `YYYY-MM-DD` and display “정확한 시각 미제공”; no `00:00` or `09:00` is invented. Same-title events inside the configured duplicate window are grouped, with official disclosures chosen as the representative. Stage inputs use a point-in-time filter: exact future articles are excluded, and same-day date-only items are conservatively excluded from intraday model inputs because their publication time cannot be proven.

Premarket feature groups are cut off strictly before 09:00. Opening-five-minute groups are unavailable before 09:05 and observations after 09:05 are excluded from the 09:05 bundle. The same underlying two-stage bundle validators remain active in production and backtests.

## Decision, Data Lab and Shadow records

Observation rank combines configurable data-completeness and directional-reference weights. It is marked experimental and `score_is_probability=false`. With the current untrained stock model, `stock_signal_enabled=false`; even complete conditions yield WATCH or WAIT, never an entry candidate.

Data Lab counts real raw/training JSONL records. Backtest metrics remain null until a stage-specific walk-forward test exists. Shadow snapshots store the state, actual decision price when present, conditions and configurable fee/slippage. A hypothetical trade is created only when an enabled, validated model emits an entry state and every required condition is met; this cannot happen with the current gate.

## Remaining limits

There is no trained stock probability model, probability calibrator or completed stock walk-forward backtest. No claim of improved predictive power is made. Continuous tick/order-book history, a sector-index provider, exact news time for date-only disclosures, and a durable external database beyond the history branch are still absent. OpenDART needs the optional `DART_API_KEY`; all existing market and deployment secrets remain unchanged.
