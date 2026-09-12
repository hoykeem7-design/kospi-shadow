# KOSPI SHADOW — Real-Time Decision Policy v6

## Objective
The primary decision question is not whether the shadow model predicts up or down. It is:

> Is buying this security **now** superior to holding cash on a risk-adjusted, cost-adjusted basis, and is buying now superior to waiting 10/30 minutes or until the next session?

## Separation of responsibilities

### Shadow model
The statistical shadow model is an independent evidence source. Its status must be reported as one of:

- `HEURISTIC`
- `SHADOW_MODEL_RESTRICTED`
- `SHADOW_MODEL`
- `VALIDATED`

If `signal_enabled=false`, benchmark superiority is absent, model execution fails, features are stale/missing, or versions mismatch:

- model trading weight = 0
- do not fabricate model probabilities or intervals
- do **not** automatically force the overall decision to `NO BUY`
- continue the real-time overlay using independently verified market information

### Real-time decision overlay
At/after the Korean cash open, a NOW decision must use current-session information where available. A 07:30 snapshot alone must never be presented as a current intraday decision.

Required evidence groups, subject to availability and timestamp validation:

1. global risk regime: S&P 500, Nasdaq, SOX, US rates, DXY, USD/KRW, VIX, oil, copper, US futures
2. Korea market regime: KOSPI/KOSDAQ, KOSPI futures/basis, breadth
3. flow: foreign/institution/retail, foreign futures, program trading
4. sector relative strength
5. security microstructure: current price, open, previous close, high/low, volume/value, VWAP or equivalent, relative strength
6. events/news: macro releases, Fed, geopolitics, earnings, filings, capital actions
7. valuation/fundamental context where decision-relevant
8. transaction cost, slippage and event risk

Missing factors receive no artificial neutral value. Remove their weight and renormalize available independent factors.

Correlated observations must not be double-counted. For example Nasdaq, SOX and a major US semiconductor stock cannot automatically count as three independent bullish factors.

## Time integrity
Every observation used for a decision must carry or inherit a defensible observation/receipt timestamp.

- pre-open briefing: use only information available by the briefing cutoff
- intraday NOW decision: use only information available at the request time
- never use later-session OHLC or future information
- unknown/stale timestamps lower confidence; critical stale inputs can block `BUY NOW`

## Action vocabulary
The final action must be exactly one of:

- `BUY NOW`
- `CONDITIONAL BUY`
- `WAIT`
- `NO BUY`
- `REDUCE`

Ambiguity defaults to `WAIT`, not to a fabricated high-confidence trade.

Candidate count: 0–2. No candidate is preferable to a forced candidate.

## Entry quality
A good company is not automatically a good entry. Penalize:

- short-term overextension
- overheated gap
- entry immediately below resistance
- price below VWAP without recovery evidence
- low-quality/low-volume advance
- intraday high chasing
- insufficient expected upside relative to invalidation distance, costs and slippage

## Risk limits
Unless a stricter account rule applies:

- maximum loss per trade: 0.5% of total capital
- normal single-name exposure: 10%
- high-volatility single-name exposure: 5%
- new same-day exposure: 20%
- minimum cash: 50%

Use structural invalidation before arbitrary fixed-percentage stops.

## Required output contract

1. `NOW`: one action from the action vocabulary
2. one-line decision: cash vs buy-now vs wait
3. market environment / opportunity / downside risk / confidence
4. three strongest positive and negative drivers (or fewer if unavailable)
5. up to two candidates with current verified price, entry condition, first/second entry, invalidation/stop, first/second target, cancellation condition and maximum weight
6. explicit wait trigger when action is `WAIT`
7. model status, model version, data-through date and `signal_enabled`
8. source and observation time for time-sensitive inputs

Never invent a price, probability, target, timestamp, flow value or model result.

## Model/decision firewall
`signal_enabled=false` means only that the SHADOW model cannot generate a trading signal. It does **not** mean all other verified evidence must produce `NO BUY`.

Conversely, a positive heuristic overlay must never be mislabeled as a validated model signal.

## Success criterion
Optimize decision quality after transaction costs and downside risk, not headline directional accuracy.