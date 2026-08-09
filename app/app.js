const $ = (id) => document.getElementById(id);
const fmtPct = (value, digits=2) => value == null ? "--" : `${(Number(value)*100).toFixed(digits)}%`;
const fmtNum = (value, digits=2) => value == null ? "--" : Number(value).toLocaleString("ko-KR", {maximumFractionDigits: digits});
const clsFor = (value) => value == null || Math.abs(value) < 1e-12 ? "neutral" : (value > 0 ? "up" : "down");
const safeText = (value, fallback="--") => value == null || value === "" ? fallback : String(value);
const escapeHtml = (value) => safeText(value, "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const safeUrl = (value) => { try { const url = new URL(String(value)); return ["http:","https:"].includes(url.protocol) ? url.href : "#"; } catch { return "#"; } };
// GitHub Actions publishes static snapshots. These are target times, not a
// promise of tick-level delivery; scheduled runs can start a few minutes late.
const MODEL_REFRESH_CHECKPOINTS = [
  ["07:30", "장전 모델·브리핑"],
  ["20:05", "마감 모델·익일 준비"]
];
const MARKET_REFRESH_WINDOWS = [
  [8 * 60, 8 * 60 + 50, 10, "NXT·시장 스냅샷"],
  [9 * 60, 15 * 60 + 50, 10, "장중 시장 스냅샷"],
  [16 * 60, 20 * 60 + 40, 20, "애프터마켓 스냅샷"]
];
const EXTRA_MARKET_REFRESHES = [
  ["09:05", "시초 5분 확인"],
  ["15:45", "마감 확인"]
];
const APP_SHELL_VERSION = "5.5.0";
const CONFIRMATION_LABELS = {
  nxt_configured_symbols_positive: "NXT 관찰 종목의 방향 확인",
  kospi200_futures_nonnegative: "KOSPI200 선물의 비약세 확인",
  kospi_spot_positive: "KOSPI 현물의 상승 확인",
  breadth_supportive: "상승 종목이 충분히 확산되는지 확인",
  large_cap_concentration_not_detected: "대형주 편중 위험이 아닌지 확인"
};

let deferredPrompt;
let currentDashboard = null;
let appShellReloading = false;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); deferredPrompt = event; $("installButton").classList.remove("hidden");
});
$("installButton").addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt(); await deferredPrompt.userChoice; deferredPrompt = null; $("installButton").classList.add("hidden");
});

function seoulParts(date=new Date()) {
  const values = {};
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone:"Asia/Seoul", year:"numeric", month:"2-digit", day:"2-digit",
    hour:"2-digit", minute:"2-digit", second:"2-digit", hourCycle:"h23", weekday:"short"
  });
  for (const part of formatter.formatToParts(date)) if (part.type !== "literal") values[part.type] = part.value;
  return {
    year:Number(values.year), month:Number(values.month), day:Number(values.day),
    hour:Number(values.hour), minute:Number(values.minute), second:Number(values.second), weekday:values.weekday
  };
}

function runtimeSession(date=new Date()) {
  const p = seoulParts(date);
  const minute = p.hour * 60 + p.minute;
  const weekday = !["Sat","Sun"].includes(p.weekday);
  if (!weekday) return {label:"주말·휴장 구간", description:"다음 영업일 장전 계획을 준비합니다."};
  if (minute < 480) return {code:"overnight_brief", label:"아침 브리핑", description:"미국 시장·야간 지표·기사 시각을 확인해 첫 관찰 대상을 준비합니다."};
  if (minute < 530) return {code:"nxt_premarket", label:"NXT 프리마켓", description:"실제 NXT 수신값과 동일 시간대 기준으로 관찰 후보를 좁힙니다."};
  if (minute < 540) return {code:"opening_auction", label:"동시호가 반영", description:"예상체결가 하나로 단정하지 않고 후보 유지 여부를 재평가합니다."};
  if (minute < 545) return {code:"opening_confirmation", label:"시초 확인 중", description:"첫 5분이 완성되기 전에는 진입 판단을 확정하지 않습니다."};
  if (minute < 570) return {code:"entry_decision", label:"진입 조건 확인", description:"장전·동시호가·첫 5분 정보를 함께 보고 조건 충족 여부를 확인합니다."};
  if (minute < 930) return {code:"intraday_management", label:"장중 관리", description:"진입 논리, 근사 VWAP, 시장·업종, 신규 재료를 체크포인트마다 확인합니다."};
  if (minute < 940) return {code:"closing_review", label:"정규장 마감 분석", description:"장전 판단과 09:05 판단의 결과를 분리해 확인합니다."};
  if (minute < 1205) return {code:"nxt_aftermarket", label:"NXT 애프터마켓", description:"KRX 종가 이후 실데이터가 있을 때만 괴리와 유동성을 표시합니다."};
  return {code:"next_day_watch", label:"다음날 관찰", description:"장 마감 후 실제 기사와 애프터마켓 수신값을 근거로 익일 확인 대상을 정리합니다."};
}

function seoulDateToInstant(year, month, day, hour, minute) {
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute));
}

function refreshScheduleForBusinessDay() {
  const schedule = new Map();
  const add = (at, label, priority=0) => {
    const prior = schedule.get(at);
    if (!prior || priority >= prior.priority) schedule.set(at, {at, label, priority});
  };
  for (const [at, label] of MODEL_REFRESH_CHECKPOINTS) add(at, label, 2);
  for (const [start, end, step, label] of MARKET_REFRESH_WINDOWS) {
    for (let minute = start; minute <= end; minute += step) {
      const at = `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
      add(at, label, 1);
    }
  }
  for (const [at, label] of EXTRA_MARKET_REFRESHES) add(at, label, 1);
  return [...schedule.values()].sort((left, right) => left.at.localeCompare(right.at));
}

function nextAutoUpdate(now=new Date()) {
  const p = seoulParts(now);
  for (let offset=0; offset<8; offset += 1) {
    const noon = seoulDateToInstant(p.year, p.month, p.day + offset, 12, 0);
    const d = seoulParts(noon);
    if (["Sat","Sun"].includes(d.weekday)) continue;
    for (const {at, label} of refreshScheduleForBusinessDay()) {
      const [hour, minute] = at.split(":").map(Number);
      const instant = seoulDateToInstant(d.year, d.month, d.day, hour, minute);
      if (instant.getTime() > now.getTime() + 30000) {
        const dayLabel = offset === 0 ? "오늘" : (offset === 1 ? "내일" : `${d.month}/${d.day}`);
        return `${dayLabel} ${at} ${label} 목표`;
      }
    }
  }
  return "다음 영업일 07:30 장전 모델·브리핑 목표";
}

function showToast(message, ok=false) {
  const toast = $("errorToast");
  toast.textContent = message;
  toast.className = `toast${ok ? " ok" : ""}`;
  setTimeout(() => toast.classList.add("hidden"), 4500);
}

function renderMarketCard(item) {
  const rate = item?.change_rate;
  return `<div class="market-item"><span class="market-name">${escapeHtml(item?.name)}</span><strong class="market-value">${fmtNum(item?.price)}</strong><span class="market-change ${clsFor(rate)}">${rate == null ? "데이터 없음" : `${rate >= 0 ? "+" : ""}${fmtPct(rate)}`}</span></div>`;
}

function fmtPp(value) {
  if (value == null || Number.isNaN(Number(value))) return "--";
  const number = Number(value) * 100;
  return `${number >= 0 ? "+" : ""}${number.toFixed(1)}%p`;
}

function renderDriver(item, tone) {
  return `<div class="driver-item ${tone}"><div class="driver-head"><strong>${escapeHtml(item?.label)}</strong><span>${fmtPp(item?.effect_probability_points)}</span></div><div class="driver-meta"><span>현재값 ${escapeHtml(safeText(item?.value_text))}</span><span>중간값 대비 민감도</span></div></div>`;
}

function renderProbabilityExplanation(data) {
  const explanation = data?.prediction?.explanation;
  if (!explanation) {
    $("explanationMethod").textContent = "설명 없음";
    $("breakdownFinal").textContent = fmtPct(data?.prediction?.probability_intraday_up, 1);
    $("breakdownPrior").textContent = "--";
    $("breakdownRaw").textContent = "--";
    $("breakdownWeight").textContent = "--";
    $("explanationSummary").textContent = "이 배포본에는 확률 분해 데이터가 없습니다. 다음 자동 배포 후 표시됩니다.";
    $("positiveDrivers").innerHTML = `<p class="empty">표시할 요인이 없습니다.</p>`;
    $("negativeDrivers").innerHTML = `<p class="empty">표시할 요인이 없습니다.</p>`;
    $("explanationNote").textContent = "";
    return;
  }
  $("explanationMethod").textContent = explanation.method === "one_feature_to_training_median" ? "학습 중간값 대비" : "모델 분해";
  $("breakdownFinal").textContent = fmtPct(explanation.final_probability, 1);
  $("breakdownPrior").textContent = fmtPct(explanation.training_prior_probability, 1);
  $("breakdownRaw").textContent = fmtPct(explanation.raw_model_probability, 1);
  $("breakdownWeight").textContent = fmtPct(explanation.model_weight, 0);
  $("explanationSummary").textContent = safeText(explanation.summary, "확률 설명을 생성하지 못했습니다.");
  const positives = explanation.positive_factors || [];
  const negatives = explanation.negative_factors || [];
  $("positiveDrivers").innerHTML = positives.length ? positives.map(item => renderDriver(item, "positive")).join("") : `<p class="empty">상승 기여가 거의 없습니다.</p>`;
  $("negativeDrivers").innerHTML = negatives.length ? negatives.map(item => renderDriver(item, "negative")).join("") : `<p class="empty">하락 기여가 거의 없습니다.</p>`;
  $("explanationNote").textContent = safeText(explanation.note, "");
}

const MISSING_LABELS = {
  no_symbols_configured: "설정된 실험 종목 없음",
  nxt_snapshot_not_received: "NXT 데이터 미수신",
  premarket_snapshot_not_collected: "프리장 데이터 미수신",
  opening_auction_data_not_received: "동시호가 데이터 미수신",
  opening_confirmation_in_progress: "시초 확인 중",
  first_five_minutes_incomplete: "첫 5분 데이터 수집 중",
  not_started: "아직 수집 전",
  insufficient_same_time_history: "기준 데이터 부족",
  baseline_median_not_positive: "기준 데이터 부족",
  current_value_missing: "데이터 없음",
  stock_level_training_and_calibration_unavailable: "확률 산출 불가",
  kis_provider_unavailable: "데이터 제공 불가",
  required_market_data_not_received: "데이터 미수신"
};

function missingLabel(reason, fallback="데이터 없음") {
  return MISSING_LABELS[reason] || fallback;
}

function metricValue(value, formatter=fmtNum, reason=null) {
  return value == null ? missingLabel(reason) : formatter(value);
}

function relativeValue(metric) {
  if (!metric?.baseline_available) return missingLabel(metric?.unavailable_reason, "기준 데이터 부족");
  return `${fmtNum(metric.relative_value, 2)}배 · ${fmtNum(metric.baseline_sample_count, 0)}일`;
}

function renderPredictionStage(prediction, statusId, listId, pendingText) {
  if (!prediction) {
    $(statusId).textContent = pendingText;
    $(listId).innerHTML = `<dt>09:30 상승 확률</dt><dd>산출 전</dd><dt>종가 상승 확률</dt><dd>산출 전</dd><dt>갭 지속 확률</dt><dd>산출 전</dd>`;
    return;
  }
  $(statusId).textContent = prediction.probability_available ? "확률 산출 완료" : missingLabel(prediction.calibration_reason, "확률 산출 불가");
  $(listId).innerHTML = [
    ["09:30 상승 확률", metricValue(prediction.open_to_0930_up_probability, value => fmtPct(value, 1), prediction.calibration_reason)],
    ["종가 상승 확률", metricValue(prediction.open_to_close_up_probability, value => fmtPct(value, 1), prediction.calibration_reason)],
    ["갭 지속 확률", metricValue(prediction.gap_continuation_probability, value => fmtPct(value, 1), prediction.calibration_reason)],
    ["신뢰도", prediction.confidence === "low" ? "낮은 신뢰도" : safeText(prediction.confidence)],
    ["표본 수", fmtNum(prediction.sample_count, 0)],
    ["확률 보정", prediction.calibration_status === "unavailable" ? "사용 불가" : safeText(prediction.calibration_status)],
    ["데이터 기준", safeText(prediction.observed_at, "수신 시각 불명")]
  ].map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function renderExperimentFactor(item, tone) {
  const actual = metricValue(item?.actual_value, value => fmtNum(value, 4));
  const reference = metricValue(item?.reference_value, value => fmtNum(value, 4));
  const contribution = item?.contribution_value == null ? "설명용 참고 신호" : fmtNum(item.contribution_value, 4);
  return `<div class="driver-item ${tone}"><div class="driver-head"><strong>${escapeHtml(item?.display_name)}</strong><span>${escapeHtml(contribution)}</span></div><div class="driver-meta"><span>실제 ${escapeHtml(actual)} · 기준 ${escapeHtml(reference)}</span><span>${escapeHtml(safeText(item?.data_quality,"품질 불명"))}</span></div></div>`;
}

function summaryRows(rows) {
  return rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("");
}

function renderPremarketSymbol(item) {
  const pre = item?.premarket_summary || {};
  const auction = item?.opening_auction_summary || {};
  const opening = item?.opening_five_minute_summary || {};
  renderPredictionStage(item?.premarket_prediction, "premarketPredictionStatus", "premarketProbabilities", "확률 산출 불가");
  renderPredictionStage(item?.post_open_0905_prediction, "postOpenPredictionStatus", "postOpenProbabilities", item?.market_phase === "opening_confirmation" ? "시초 확인 중" : "업데이트 확률 산출 전");

  $("premarketMetrics").innerHTML = summaryRows([
    ["NXT 수익률", metricValue(pre.nxt_return, value => fmtPct(value, 2), pre.unavailable_reason)],
    ["고가·저가", pre.nxt_high == null || pre.nxt_low == null ? missingLabel(pre.unavailable_reason) : `${fmtNum(pre.nxt_high)} · ${fmtNum(pre.nxt_low)}`],
    ["최종가", metricValue(pre.nxt_final_price, fmtNum, pre.unavailable_reason)],
    ["누적 거래량", metricValue(pre.cumulative_volume, value => fmtNum(value, 0), pre.unavailable_reason)],
    ["누적 거래대금", metricValue(pre.cumulative_turnover, value => fmtNum(value, 0), pre.unavailable_reason)],
    ["상대거래량", relativeValue(pre.relative_volume)],
    ["상대거래대금", relativeValue(pre.relative_turnover)],
    ["거래대금÷시가총액", metricValue(pre.turnover_to_market_cap, value => fmtPct(value, 3))],
    ["호가 스프레드", metricValue(pre.bid_ask_spread, value => fmtPct(value, 3))],
    ["호가 불균형", metricValue(pre.orderbook_imbalance, value => fmtPct(value, 1))],
    ["체결 불균형", metricValue(pre.execution_imbalance, value => fmtPct(value, 1))],
    ["재료", pre.material?.availability === "available" ? safeText(pre.material.material_type) : "데이터 제공 불가"],
    ["기준 시각", safeText(pre.observed_at, "수신 시각 불명")],
    ["데이터 지연", pre.data_delay_seconds == null ? "수신 시각 불명" : `${fmtNum(pre.data_delay_seconds, 0)}초`],
    ["데이터 품질", safeText(pre.data_quality, "데이터 품질 낮음")]
  ]);
  $("auctionMetrics").innerHTML = summaryRows([
    ["예상체결가", metricValue(auction.expected_price, fmtNum, auction.unavailable_reason)],
    ["예상체결수량", metricValue(auction.expected_volume, value => fmtNum(value, 0), auction.unavailable_reason)],
    ["예상체결대금", metricValue(auction.expected_turnover, value => fmtNum(value, 0), auction.unavailable_reason)],
    ["가격 안정도", auction.expected_price_stability?.available ? fmtPct(auction.expected_price_stability.value, 1) : missingLabel(auction.expected_price_stability?.unavailable_reason, "데이터 부족")],
    ["수량 변화", metricValue(auction.expected_volume_change, value => fmtPct(value, 1))],
    ["마지막 1분 가격 변동폭", auction.last_1m_collection_status === "unavailable" ? "미수집" : metricValue(auction.last_1m_expected_price_range, fmtNum)],
    ["마지막 1분 수량 변화", auction.last_1m_collection_status === "unavailable" ? "미수집" : metricValue(auction.last_1m_expected_volume_change, value => fmtPct(value, 1))],
    ["업데이트 수", fmtNum(auction.update_count, 0)],
    ["관측 구간", auction.observation_start && auction.observation_end ? `${auction.observation_start} ~ ${auction.observation_end}` : "데이터 미수신"]
  ]);
  $("openingMetrics").innerHTML = summaryRows([
    ["실제 시가", metricValue(opening.actual_open, fmtNum, opening.unavailable_reason)],
    ["첫 1분 수익률", metricValue(opening.first_1m_return, value => fmtPct(value, 2), opening.unavailable_reason)],
    ["첫 3분 수익률", metricValue(opening.first_3m_return, value => fmtPct(value, 2), opening.unavailable_reason)],
    ["첫 5분 수익률", metricValue(opening.first_5m_return, value => fmtPct(value, 2), opening.unavailable_reason)],
    ["첫 5분 거래량", metricValue(opening.volume, value => fmtNum(value, 0), opening.unavailable_reason)],
    ["상대거래량", relativeValue(opening.relative_volume)],
    ["근사 VWAP", metricValue(opening.approximate_vwap, fmtNum, opening.unavailable_reason)],
    ["근사 VWAP 대비", metricValue(opening.current_vs_approximate_vwap, value => fmtPct(value, 2), opening.unavailable_reason)],
    ["시가 유지", opening.open_held == null ? "데이터 부족" : (opening.open_held ? "유지" : "이탈")],
    ["이탈 후 회복", opening.open_recovery == null ? "데이터 부족" : (opening.open_recovery ? "회복" : "미회복")]
  ]);
  const positives = item?.positive_factors || [];
  const negatives = item?.negative_factors || [];
  $("premarketPositiveFactors").innerHTML = positives.length ? positives.map(factor => renderExperimentFactor(factor, "positive")).join("") : `<p class="empty">확인된 상승 참고 요인이 없습니다.</p>`;
  $("premarketNegativeFactors").innerHTML = negatives.length ? negatives.map(factor => renderExperimentFactor(factor, "negative")).join("") : `<p class="empty">확인된 하락·위험 참고 요인이 없습니다.</p>`;
  const warnings = item?.data_availability?.warnings || [];
  $("premarketDataNote").textContent = warnings.length ? `데이터 경고: ${warnings.map(reason => missingLabel(reason, reason)).join(", ")}` : "모델 기여도를 계산할 학습 모델이 없어 현재 요인은 설명용 참고 신호로만 표시합니다.";
}

function renderPremarketExperiment(data) {
  const experiment = data?.premarket_experiment;
  $("premarketPhase").textContent = safeText(experiment?.phase_display, "데이터 확인 중");
  const symbols = experiment?.symbols || [];
  if (!experiment || !symbols.length) {
    $("premarketEmpty").textContent = missingLabel(experiment?.data_availability?.unavailable_reason, "이 배포본에는 개별 종목 실험 데이터가 없습니다.");
    $("premarketEmpty").classList.remove("hidden");
    $("premarketContent").classList.add("hidden");
    return;
  }
  $("premarketEmpty").classList.add("hidden");
  $("premarketContent").classList.remove("hidden");
  const select = $("premarketSymbolSelect");
  const previous = select.value;
  select.innerHTML = symbols.map(item => `<option value="${escapeHtml(item.symbol)}">${escapeHtml(item.name)} (${escapeHtml(item.symbol)})</option>`).join("");
  select.value = symbols.some(item => item.symbol === previous) ? previous : symbols[0].symbol;
  const renderSelected = () => renderPremarketSymbol(symbols.find(item => item.symbol === select.value) || symbols[0]);
  select.onchange = renderSelected;
  renderSelected();
}

function conditionList(items, empty="확인 조건이 없습니다.") {
  if (!(items || []).length) return `<p class="empty">${escapeHtml(empty)}</p>`;
  return `<ul class="condition-list">${items.map(item => {
    const status = item?.status || "pending";
    const label = typeof item === "string" ? item : item?.label;
    return `<li class="condition-${escapeHtml(status)}"><span>${status === "met" ? "✓" : status === "not_met" ? "!" : "·"}</span>${escapeHtml(label)}</li>`;
  }).join("")}</ul>`;
}

function supplyStateLabel(state) {
  return ({
    BROAD_POSITIVE:"동반 상승 관찰",
    BROAD_NEGATIVE:"동반 약세",
    SINGLE_NAME:"단일 종목",
    MIXED:"방향 혼재",
    UNAVAILABLE:"수급 미수신"
  })[state] || safeText(state, "수급 확인 중");
}

function fmtKrwCompact(value) {
  if (value == null || Number.isNaN(Number(value))) return "거래대금 --";
  const amount = Number(value);
  if (Math.abs(amount) >= 1000000000000) return `거래대금 ${fmtNum(amount / 1000000000000,1)}조원`;
  if (Math.abs(amount) >= 100000000) return `거래대금 ${fmtNum(amount / 100000000,0)}억원`;
  return `거래대금 ${fmtNum(amount / 10000,0)}만원`;
}

function renderMarketAttention(attention) {
  const leaders = attention?.leaders || [];
  const available = attention?.availability === "available" && leaders.length > 0;
  $("marketAttentionStatus").textContent = available ? `${safeText(attention.market,"시장")} ${fmtNum(leaders.length,0)}종목 수신` : "시장 순위 미수신";
  $("marketAttentionStatus").className = `badge ${available ? "fresh" : "warning-badge"}`;
  $("marketAttentionNote").textContent = safeText(attention?.note, "포털 조회수가 아니라 실제 거래대금·거래량 증가 순위입니다.");
  $("marketAttentionEmpty").classList.toggle("hidden", available);
  $("marketAttentionList").innerHTML = available ? leaders.slice(0,5).map((item,index) => {
    const ranks = item?.ranks || {};
    const badges = [
      ranks.turnover == null ? null : `거래대금 #${fmtNum(ranks.turnover,0)}`,
      ranks.volume_growth == null ? null : `거래증가 #${fmtNum(ranks.volume_growth,0)}`
    ].filter(Boolean);
    return `<article class="market-attention-item">
      <div class="market-attention-rank">#${index+1}</div>
      <div class="market-attention-copy"><strong>${escapeHtml(item?.name)}</strong><small>${escapeHtml(item?.symbol)} · ${escapeHtml(fmtKrwCompact(item?.cumulative_turnover))}</small><div>${badges.map(value => `<span>${escapeHtml(value)}</span>`).join("")}</div></div>
      <div class="market-attention-move"><strong class="${clsFor(item?.current_return)}">${item?.current_return == null ? "--" : fmtPct(item.current_return,2)}</strong><small>${item?.volume_growth_rate == null ? "증가율 --" : `거래량 ${fmtPct(item.volume_growth_rate,0)}`}</small></div>
    </article>`;
  }).join("") : "";
}

function themeMemberRow(member) {
  const role = member?.role === "LEADER_OBSERVATION" ? "대장 관찰" : "후속 관찰";
  const turnoverRank = member?.market_attention_ranks?.turnover;
  const relative = member?.relative_turnover == null
    ? (turnoverRank == null ? fmtKrwCompact(member?.cumulative_turnover) : `시장 거래대금 #${fmtNum(turnoverRank,0)}`)
    : `상대거래대금 ${fmtNum(member.relative_turnover,2)}배`;
  return `<div class="theme-member"><div><strong>${escapeHtml(member?.name)}</strong><small>${escapeHtml(member?.symbol)} · ${escapeHtml(role)}</small></div><div><span class="${clsFor(member?.active_return)}">${member?.active_return == null ? "방향 미수신" : fmtPct(member.active_return,2)}</span><small>${escapeHtml(relative)}</small></div></div>`;
}

function renderThemeCard(theme) {
  const supply = theme?.supply || {};
  const attention = theme?.attention || {};
  const alignment = theme?.global_alignment || {};
  const previousClose = theme?.previous_close_context || {};
  const weather = theme?.weather_event || {};
  const members = theme?.members || [];
  const catalysts = theme?.catalysts || [];
  const blockers = theme?.blockers || [];
  const actionClass = theme?.action === "CHASE_REVIEW" ? "chase" : (theme?.action === "OBSERVE" ? "observe" : "wait");
  const weatherLabel = weather.availability === "available"
    ? `${safeText(weather.location,"서울")} ${weather.temperature_c == null ? "날씨" : `${fmtNum(weather.temperature_c,1)}℃`}${weather.maximum_temperature_c == null ? "" : ` · 최고 ${fmtNum(weather.maximum_temperature_c,1)}℃`}`
    : (weather.availability === "news_proxy" ? "날씨 기사 프록시" : null);
  const chips = [
    supplyStateLabel(supply.state),
    `구성 ${fmtNum(supply.member_count,0)}종목`,
    supply.relative_turnover_median == null ? "상대거래대금 미수신" : `상대거래대금 중앙값 ${fmtNum(supply.relative_turnover_median,2)}배`,
    attention.availability === "proxy" ? `기사·공시 프록시 ${fmtNum(attention.news_count,0)}건` : (attention.availability === "market_flow" ? `시장 순위 ${fmtNum(attention.market_ranked_member_count,0)}종목` : "관심도 미연결"),
    previousClose.availability === "available" ? "전일 종가 기준 확인" : "전일 종가 미수신",
    alignment.availability === "available" ? alignment.label : null,
    weatherLabel
  ].filter(Boolean);
  return `<article class="theme-card ${actionClass}">
    <div class="theme-card-head"><div><span class="rank">#${fmtNum(theme?.rank,0)}</span><h3>${escapeHtml(theme?.label)}</h3></div><span class="theme-action ${actionClass}">${escapeHtml(theme?.action_label)}</span></div>
    <div class="theme-chip-row">${chips.map(value => `<span>${escapeHtml(value)}</span>`).join("")}</div>
    <div class="theme-members">${members.length ? members.map(themeMemberRow).join("") : `<p class="empty">연결된 설정 종목이 없습니다.</p>`}</div>
    <details class="theme-evidence"><summary>근거와 보류 조건</summary><div class="theme-evidence-grid"><section><h4>체크포인트 이전 재료</h4>${catalysts.length ? `<ul>${catalysts.map(item => `<li>${escapeHtml(item.title)}</li>`).join("")}</ul>` : `<p>분류 가능한 기사·공시 없음</p>`}</section><section><h4>보류·추격 위험</h4>${blockers.length ? `<ul>${blockers.slice(0,4).map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul>` : `<p>추가 보류 조건 없음</p>`}</section></div></details>
  </article>`;
}

function renderThemeSupplyRadar(coach, gateLocked) {
  const radar = coach?.theme_supply_radar || {};
  const themes = radar?.themes || [];
  const sources = radar?.source_availability || {};
  const universe = radar?.universe || {};
  const marketAttention = radar?.market_attention || {};
  const status = radar.availability === "available" ? "근거 수신" : (radar.availability === "partial" ? "일부 수신" : "데이터 없음");
  $("themeRadarStatus").textContent = `${status} · Shadow 전용`;
  $("themeRadarStatus").className = `badge ${radar.availability === "available" ? "fresh" : "warning-badge"}`;
  $("themeRadarSummary").textContent = safeText(radar.summary, "테마·수급 근거를 확인하지 못했습니다.");
  $("themeRadarScope").textContent = `설정 ${fmtNum(universe.configured_symbol_count,0)}개 + 시장순위 ${fmtNum(universe.market_attention_symbol_count,0)}개`;
  const availableSources = [
    sources.nxt_supply === "available" ? "NXT 수급" : null,
    sources.market_turnover_ranking === "available" ? "KIS 시장순위" : null,
    sources.theme_news_and_disclosures === "available" ? "기사·공시" : null,
    sources.previous_us_market === "available" ? "전일 미국장" : null,
    sources.weather_observation_or_forecast === "available" ? "날씨" : null
  ].filter(Boolean);
  $("themeRadarSources").textContent = availableSources.length ? `수신: ${availableSources.join(" · ")}` : "핵심 출처 미수신";
  $("themeRadarGate").textContent = gateLocked ? `KOSPI Gate ${safeText(radar.kospi_gate_status,"UNAVAILABLE")} · 진입 잠금` : "Gate 통과 여부와 무관하게 검증 전 관찰만";
  renderMarketAttention(marketAttention);
  $("themeRadarEmpty").classList.toggle("hidden", themes.length > 0);
  $("themeRadarList").innerHTML = themes.map(renderThemeCard).join("");
}

function renderDecisionCard(card, gateLocked=false, index=0) {
  const watch = (card?.why_watch || []).map(value => `<li>${escapeHtml(value)}</li>`).join("") || `<li>실데이터 수신 여부 확인</li>`;
  const risks = (card?.risk_factors || []).map(value => `<li>${escapeHtml(value)}</li>`).join("") || `<li>검증된 종목 확률 모델 없음</li>`;
  const displayAction = gateLocked ? "관찰만 · 진입 잠금" : safeText(card?.action_label, "조건 확인");
  const open = !gateLocked && index === 0 ? " open" : "";
  const theme = card?.theme_supply || {};
  const themeStrip = theme?.primary_theme ? `<div class="candidate-theme-strip"><span>${escapeHtml(theme.primary_theme)}</span><span>${escapeHtml(theme.role === "LEADER_OBSERVATION" ? "대장 관찰" : "후속 관찰")}</span><span>${escapeHtml(supplyStateLabel(theme.supply_state))}</span>${theme.chase_risk ? `<span class="chase-risk">추격 검토</span>` : ""}</div>` : "";
  return `<details class="decision-card${gateLocked ? " locked" : ""}"${open}>
    <summary><div class="decision-card-summary-main"><span class="rank">#${fmtNum(card?.candidate_rank,0)}</span><h3>${escapeHtml(card?.name)} <small>${escapeHtml(card?.symbol)}</small></h3></div><span class="action-state action-${escapeHtml(String(card?.action_state||"WAIT").toLowerCase())}">${escapeHtml(displayAction)}</span></summary>
    <div class="decision-card-body">
    ${themeStrip}
    <div class="decision-meta"><span>관찰 점수 ${card?.observation_score == null ? "산출 불가" : `${fmtNum(card.observation_score,1)}점`}</span><span>완전성 ${fmtPct(card?.data_completeness,0)}</span><span>품질 ${escapeHtml(safeText(card?.data_quality,"불명"))}</span><span>확률 산출 불가</span></div>
    <div class="decision-columns">
      <section><h4>왜 주목하는가</h4><ul>${watch}</ul><h4 class="risk-title">위험 요인</h4><ul>${risks}</ul></section>
      <section><h4>언제 진입을 검토하는가</h4><p class="condition-window">${escapeHtml(card?.entry_window)}</p>${conditionList(card?.entry_trigger_conditions)}</section>
      <section><h4>언제 축소·청산을 검토하는가</h4>${conditionList([...(card?.invalidation_conditions||[]), ...(card?.reduce_conditions||[]), ...(card?.exit_conditions||[])])}</section>
    </div>
    <div class="decision-foot"><span>KOSPI Gate: ${escapeHtml(safeText(card?.kospi_gate_status,"UNAVAILABLE"))}</span><span>상태 변화: ${escapeHtml(safeText(card?.state_update?.change_reason,"첫 스냅샷"))}</span><span>동시호가: ${escapeHtml(card?.auction_transition?.label || "미수신")}</span><span>다음 확인: ${escapeHtml(safeText(card?.next_review_at,"미정"))}</span><span class="experimental-label">실험적 신호</span></div>
    </div>
  </details>`;
}

function renderKospiMarketGate(data) {
  const coach = data?.decision_coach_v5 || {};
  const gate = coach?.kospi_market_gate || {};
  const status = safeText(gate.status, "UNAVAILABLE");
  const statusNode = $("marketGateStatus");
  statusNode.textContent = `${status} · ${safeText(gate.status_label, "판단 불가")}`;
  statusNode.className = `gate-status gate-${status.toLowerCase()}`;
  const reasons = gate?.abstention?.reasons || [];
  $("marketGateSummary").textContent = reasons[0] || "필수 모델·시장 데이터를 확인하지 못했습니다.";

  const lab = coach?.kospi_model_lab || gate?.model_lab || {};
  const signalEnabled = Boolean(lab.signal_enabled ?? gate.signal_enabled);
  const breadth = gate?.market_breadth || {};
  const concentration = breadth?.large_cap_concentration || {};
  const evidence = [
    ...reasons,
    signalEnabled ? "모델 승격 게이트 통과" : "모델 미승격 · 확률은 매매 판단에 사용하지 않음",
    breadth.availability === "available" ? safeText(breadth.label) : "시장 폭 데이터 미수신",
    concentration.availability === "inferred" ? safeText(concentration.label) : null
  ].filter(Boolean).filter((value, index, values) => values.indexOf(value) === index).slice(0, 3);
  $("marketGateReasons").innerHTML = evidence.map(value => `<li>${escapeHtml(value)}</li>`).join("") || `<li>판단 근거를 확인하지 못했습니다.</li>`;

  const pendingConfirmations = Object.entries(gate?.confirmations || {})
    .filter(([, confirmed]) => confirmed !== true)
    .map(([name]) => CONFIRMATION_LABELS[name] || name);
  const conditions = [];
  if (!signalEnabled) conditions.push("모델 승격 기준 통과");
  if (gate?.checkpoint?.code !== "open_confirmation") conditions.push("09:05 현물 첫 5분·선물·시장 폭 확인");
  conditions.push(...pendingConfirmations);
  if (status === "RISK_OFF") conditions.unshift("확률과 현물·선물 약세가 해소되는지 재확인");
  const uniqueConditions = conditions.filter((value, index, values) => values.indexOf(value) === index).slice(0, 3);
  $("marketGateConditions").innerHTML = (uniqueConditions.length ? uniqueConditions : ["현재 조건이 다음 체크포인트에서도 유지되는지 확인"])
    .map(value => `<li>${escapeHtml(value)}</li>`).join("");

  const sessionProbability = gate?.session_close_up_probability || {};
  $("sessionCloseProbability").textContent = sessionProbability.availability === "available" ? fmtPct(sessionProbability.probability, 1) : "산출 불가";
  $("sessionCloseProbabilityUse").textContent = signalEnabled ? "승격 모델 · 시가→종가" : "연구값 · 매매 판단 미사용";
  $("sessionProbabilityMetric").classList.toggle("model-locked", !signalEnabled);
  const remainingProbability = gate?.current_to_close_up_probability || {};
  $("remainingSessionProbability").textContent = remainingProbability.availability === "available" ? fmtPct(remainingProbability.probability, 1) : "산출 불가";
  $("remainingSessionReason").textContent = remainingProbability.availability === "available" ? "현재 시점 기준" : "별도 잔여장 모델 미학습";

  $("marketBreadth").textContent = breadth.availability === "available" ? `${fmtPct(breadth.advancer_ratio, 0)} · ${safeText(breadth.label)}` : "데이터 미수신";
  $("marketBreadthDetail").textContent = breadth.availability === "available" ? `상승 ${fmtNum(breadth.advancers,0)} · 하락 ${fmtNum(breadth.decliners,0)}` : "상승·하락 종목 수 미수신";
  $("largeCapConcentration").textContent = concentration.availability === "inferred" ? (concentration.risk ? "편중 위험" : "확산 확인") : "판단 보류";

  const checkpoints = gate?.checkpoints || [];
  $("marketGateCheckpoints").innerHTML = checkpoints.length ? checkpoints.map(item => `<div class="gate-checkpoint ${escapeHtml(item.status)}"><strong>${escapeHtml(item.at)}</strong><span>${escapeHtml(item.purpose)}</span></div>`).join("") : `<div class="empty-state">체크포인트 정보가 없습니다.</div>`;
  $("marketGateAction").textContent = safeText(gate.action, "매매 보류");
  const abstention = $("marketGateAbstention");
  abstention.className = `abstention${gate?.abstention?.active ? " active" : ""}`;
  abstention.querySelector("strong").textContent = safeText(gate?.abstention?.label, "매매 보류");
  $("marketGateReason").textContent = reasons.join(" · ") || "조건부 검토";

  $("dockGateStatus").textContent = `${status} · ${safeText(gate.status_label, "판단 불가")}`;
  $("dockAction").textContent = safeText(gate.action, "매매 보류");
  $("dockNextCheckpoint").textContent = `${safeText(data?.coaching?.next_checkpoint_at, gate?.checkpoint?.at || "미정")} ${safeText(data?.coaching?.next_checkpoint_label, "재확인")}`;

  $("kospiModelStatus").textContent = lab.signal_enabled ? "signal_enabled=true" : "signal_enabled=false";
  $("kospiModelStatus").className = `badge ${lab.signal_enabled ? "up" : "warning-badge"}`;
  const validation = lab.validation || {};
  const failedChecks = lab.failed_checks || [];
  const metrics = [
    ["목표", safeText(lab.target_definition, "KOSPI 종가 > 당일 시가")],
    ["승격 상태", safeText(lab.promotion_status, "미확인")],
    ["검증 표본", `${fmtNum(validation.oos_n,0)}개`],
    ["ROC-AUC", fmtNum(validation.roc_auc,4)],
    ["Brier 개선", fmtNum(validation.brier_improvement,6)],
    ["비용 반영 Sharpe", fmtNum(validation.strategy_sharpe,3)],
    ["실패 기준", failedChecks.length ? failedChecks.join(", ") : "없음"],
    ["현재→종가 모델", lab?.remaining_session_model?.availability === "available" ? "사용 가능" : "미학습·미검증"]
  ];
  $("kospiModelMetrics").innerHTML = metrics.map(([label,value]) => `<div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(value)}</span></div>`).join("");
  $("kospiModelScope").textContent = safeText(lab.probability_scope, "당일 시가→종가 확률과 현재→종가 확률을 구분합니다.");

  const ledger = coach?.live_prediction_ledger || {};
  const records = ledger.records || [];
  $("kospiPredictionLedger").innerHTML = records.length ? records.map(item => {
    const generated = item.generated_at ? new Date(item.generated_at).toLocaleString("ko-KR", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",timeZone:"Asia/Seoul"}) : "--";
    const concentrationLabel = item.large_cap_concentration_risk == null ? "--" : (item.large_cap_concentration_risk ? "위험" : "아님");
    return `<tr><td>${escapeHtml(generated)}</td><td>${escapeHtml(safeText(item.checkpoint))}</td><td>${escapeHtml(safeText(item.gate_status))}</td><td>${item.session_close_up_probability == null ? "--" : fmtPct(item.session_close_up_probability,1)}</td><td>${item.current_to_close_up_probability == null ? "--" : fmtPct(item.current_to_close_up_probability,1)}</td><td>${item.advancer_ratio == null ? "--" : fmtPct(item.advancer_ratio,0)}</td><td>${escapeHtml(concentrationLabel)}</td><td>${item.abstained ? "보류" : "조건부"}</td></tr>`;
  }).join("") : `<tr><td colspan="8">아직 저장된 라이브 예측 원장이 없습니다.</td></tr>`;
  $("kospiLedgerStatus").textContent = `원장 ${fmtNum(ledger.record_count,0)}건 · 종가 결과 라벨은 공식 종가 수신 후 별도 평가`;
}

function renderDecisionCoach(data) {
  const coach = data?.decision_coach_v5;
  const session = runtimeSession();
  const phase = coach?.phase || {};
  const gate = coach?.kospi_market_gate || {};
  const signalGate = coach?.signal_gate || {};
  const marketGateAllowsEntries = Boolean(gate.stock_entries_allowed);
  const stockSignalEnabled = Boolean(signalGate.stock_signal_enabled);
  const gateLocked = !(marketGateAllowsEntries && stockSignalEnabled);
  renderThemeSupplyRadar(coach, gateLocked);
  $("decisionPhase").textContent = safeText(phase.display, session.label);
  $("decisionPhaseDescription").textContent = session.description;
  const cards = coach?.decision_cards || [];
  const top = cards[0];
  const topTheme = coach?.theme_supply_radar?.themes?.[0];
  const topThemeMember = topTheme?.members?.[0];
  $("whatToWatch").textContent = topTheme
    ? `${topTheme.label}${topThemeMember ? ` · ${topThemeMember.name}` : ""} · 관찰 전용`
    : top ? `${top.name} (${top.symbol}) · ${gateLocked ? "관찰만" : top.action_label}` : "설정된 종목 또는 실데이터 없음";
  $("whenToEnter").textContent = gateLocked ? `KOSPI Gate ${safeText(gate.status,"UNAVAILABLE")} 해제 전 신규 진입 보류` : (top?.entry_window || "09:05 완성 데이터 확인 후 조건부 검토");
  $("whenToExit").textContent = top?.invalidation_conditions?.[0]?.label || "진입 논리와 구조적 기준 재확인";
  const checks = coach?.market_environment?.top_checks || [];
  $("topChecks").innerHTML = checks.length ? checks.map(value => `<span>${escapeHtml(value)}</span>`).join("") : `<span>데이터 부족</span>`;
  const candidateNotice = $("candidateGateNotice");
  candidateNotice.className = `candidate-gate-notice ${gateLocked ? "locked" : "allowed"}`;
  candidateNotice.textContent = !marketGateAllowsEntries
    ? `KOSPI Gate ${safeText(gate.status,"UNAVAILABLE")} · 신규 진입 잠금. 아래 종목은 관찰용이며 주문 후보가 아닙니다.`
    : !stockSignalEnabled
      ? `KOSPI Gate ${safeText(gate.status)} 통과 · 종목 모델 미학습·미승격으로 신규 진입 잠금. 아래 종목은 관찰 전용입니다.`
      : `KOSPI Gate ${safeText(gate.status)} 및 종목 모델 승격 통과 · 검증된 조건을 충족한 종목만 제한적으로 검토합니다.`;
  $("candidateCount").textContent = gateLocked ? `관찰 후보 ${fmtNum(cards.length,0)}개 · 진입 잠금` : `조건부 후보 ${fmtNum(cards.length,0)}개`;
  $("decisionCardList").classList.toggle("is-locked", gateLocked);
  $("decisionCardList").innerHTML = cards.length ? cards.map((card, index) => renderDecisionCard(card, gateLocked, index)).join("") : `<div class="empty-state">PREMARKET_SYMBOLS와 실제 수신 데이터가 없어 종목 의사결정 카드를 생성하지 않았습니다.</div>`;

  const closing = coach?.closing_review || {};
  $("closingReview").innerHTML = closing.availability === "available" ? (closing.symbols || []).map(item => `<div class="mini-watch"><strong>${escapeHtml(item.name)}</strong><span>시가 ${fmtNum(item.actual_open)} · 종가 ${fmtNum(item.close_price)}</span></div>`).join("") : `<p class="empty">마감 라벨 미완성 · ${escapeHtml(safeText(closing.unavailable_reason,"데이터 없음"))}</p>`;
  const after = coach?.nxt_aftermarket || {};
  $("aftermarketReview").innerHTML = after.availability === "available" ? (after.symbols || []).map(item => `<div class="mini-watch"><strong>${escapeHtml(item.name)}</strong><span>${fmtNum(item.current_price)} · KRX 대비 ${fmtPct(item.krx_close_return,2)}</span></div>`).join("") : `<p class="empty">데이터 미수신<br><small>${escapeHtml(safeText(after.unavailable_reason))}</small></p>`;
  const nextDay = coach?.next_day_watchlist || [];
  $("nextDayWatch").innerHTML = nextDay.length ? nextDay.map(item => `<div class="mini-watch"><strong>#${fmtNum(item.rank,0)} ${escapeHtml(item.name)}</strong><span>${escapeHtml(item.status)}${item.close_gap == null ? "" : ` · ${fmtPct(item.close_gap,2)}`}</span></div>`).join("") : `<p class="empty">실제 장 마감 후 재료 또는 애프터마켓 데이터가 없어 후보를 만들지 않았습니다.</p>`;

  const lab = coach?.data_lab || {};
  const rows = lab.symbols || [];
  $("dataLabBody").innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(row.name)}<small>${escapeHtml(row.symbol)}</small></td><td>${fmtNum(row.collected_trading_days,0)}</td><td>${fmtNum(row.premarket_sample_count,0)}</td><td>${fmtNum(row.opening_auction_sample_count,0)}</td><td>${fmtNum(row.opening_five_minute_sample_count,0)}</td><td>${fmtNum(row.label_0930_count,0)}</td><td>${fmtNum(row.close_label_count,0)}</td><td>${fmtPct(row.data_completeness,0)}</td><td>${fmtNum(row.trading_days_remaining,0)}일</td></tr>`).join("") : `<tr><td colspan="9">수집된 종목 이력이 없습니다.</td></tr>`;
  const models = lab.models || {};
  $("modelLab").innerHTML = Object.entries(models).map(([name, metrics]) => `<div><strong>${escapeHtml(name)}</strong><span>${metrics.availability === "available" ? `Brier ${fmtNum(metrics.brier_score,4)}` : "워크포워드 백테스트 미수행 · 지표 없음"}</span></div>`).join("") || `<p class="empty">모델 검증 지표가 없습니다.</p>`;

  const snapshots = coach?.shadow_trading?.snapshots || [];
  $("shadowRecords").innerHTML = snapshots.length ? snapshots.slice(0,10).map(item => `<div class="list-item"><strong>${escapeHtml(item.symbol)} · ${escapeHtml(item.action_state)}</strong><span class="list-meta"><span>${escapeHtml(item.stage)}</span><span>${item.hypothetical_trade_created ? "가상매매 생성" : "진입 조건 미충족 · 가상매매 없음"}</span><span>${escapeHtml(item.decision_id)}</span></span></div>`).join("") : `<p class="empty">저장된 의사결정 스냅샷이 없습니다.</p>`;

  const operations = coach?.operations || {};
  $("versionBadge").textContent = `v${safeText(operations.app_version, data?.app_version || APP_SHELL_VERSION)}`;
  const publishedAt = operations.last_pages_deploy || data?.generated_at_seoul;
  const collectedAt = operations.last_market_refresh || operations.last_data_collection || data?.generated_at_seoul;
  $("operationsMetrics").innerHTML = summaryRows([
    ["build SHA", safeText(operations.build_sha || data?.build_sha, "로컬/미제공")],
    ["배포 채널", safeText(operations.publish_target, "GitHub Pages")],
    ["현재 Pages 스냅샷 생성", safeText(publishedAt, "생성 시각 미제공")],
    ["마지막 시장 수집", safeText(collectedAt, "수집 시각 미제공")],
    ["마지막 정상 워크플로", safeText(operations.last_successful_workflow, "상태 API 미연결")],
    ["다음 체크포인트", `${safeText(operations.next_scheduled_checkpoint?.at,"미정")} ${safeText(operations.next_scheduled_checkpoint?.label,"")}`],
    ["자동 수집", "장중 10분 · 애프터마켓 20분 목표"],
    ["앱 갱신", "데이터 확인 + 앱 화면 버전 재검사"]
  ]);

  const disclosure = coach?.official_disclosure || {};
  $("disclosureAvailability").textContent = disclosure.availability === "available" ? "OpenDART 공식 공시 수신" : `공시 ${missingLabel(disclosure.unavailable_reason,"데이터 제공 불가")}`;
}

function renderFreshness(data) {
  const received = [data?.market?.kospi, data?.market?.kospi200_futures]
    .map(item => new Date(item?.received_at || item?.observed_at || ""))
    .filter(value => !Number.isNaN(value.getTime()));
  const generated = new Date(data?.generated_at_seoul || "");
  const reference = received.length ? new Date(Math.min(...received.map(value => value.getTime()))) : generated;
  const ageMinutes = Number.isNaN(reference.getTime()) ? null : Math.max(0, Math.floor((Date.now() - reference.getTime()) / 60000));
  const threshold = Number(data?.operational_trust?.market_stale_after_minutes || 15);
  const fresh = ageMinutes != null && ageMinutes <= threshold;
  const label = received.length === 2 ? "시장 수신" : "스냅샷";
  $("freshnessBadge").textContent = fresh ? `최신 ${label} · ${ageMinutes}분 전` : `주의 · ${label} ${ageMinutes == null ? "시각 미제공" : `${ageMinutes}분 전`}`;
  $("freshnessBadge").className = `badge ${fresh ? "fresh" : "stale"}`;
  $("nextAutoUpdate").textContent = `다음 자동 갱신 ${nextAutoUpdate()}`;
}

function render(data) {
  currentDashboard = data;
  const session = runtimeSession();
  const rawProbability = data?.prediction?.probability_intraday_up;
  const p = rawProbability == null ? null : Number(rawProbability);
  const probabilityAvailable = data?.prediction?.probability_available !== false && p != null && Number.isFinite(p);
  $("sessionBadge").textContent = session.label;
  $("generatedAt").textContent = new Date(data.generated_at_seoul).toLocaleString("ko-KR", {hour:"2-digit",minute:"2-digit",month:"numeric",day:"numeric",timeZone:"Asia/Seoul"});
  $("sessionTitle").textContent = data.coaching.headline;
  $("sessionDescription").textContent = session.description;
  renderFreshness(data);
  $("probability").textContent = probabilityAvailable ? fmtPct(p,1) : "--";
  $("gauge").classList.toggle("unavailable", !probabilityAvailable);
  $("gauge").style.setProperty("--p", probabilityAvailable ? Math.max(0,Math.min(100,p*100)) : 0);
  $("targetDate").textContent = data.prediction.candidate_target_date;
  const direction = probabilityAvailable ? safeText(data.prediction.research_direction,"FLAT") : "UNAVAILABLE";
  $("directionBadge").textContent = probabilityAvailable ? direction : "확률 산출 불가";
  $("directionBadge").className = `direction ${probabilityAvailable ? direction.toLowerCase() : "flat"}`;
  $("coachHeadline").textContent = data.coaching.headline;
  $("coachRationale").textContent = data.coaching.rationale;
  $("timingScore").textContent = `${fmtNum(data.coaching.timing_score,0)}점`;
  $("confidenceLabel").textContent = data.coaching.confidence_label;
  $("nextCheckpoint").textContent = `${data.coaching.next_checkpoint_at} ${data.coaching.next_checkpoint_label}`;
  renderProbabilityExplanation(data);
  renderPremarketExperiment(data);
  renderKospiMarketGate(data);
  renderDecisionCoach(data);
  $("dataSource").textContent = `${safeText(data.data_quality.latest_source)} · ${safeText(data.data_quality.target_date_max)}`;
  $("briefingList").innerHTML = (data.briefing || []).length ? (data.briefing || []).map(item => `<div class="briefing-item"><strong><span class="briefing-mark ${item.tone}"></span>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.text)}</p></div>`).join("") : `<p class="empty">핵심 요약을 생성하지 못했습니다.</p>`;

  const market = [data.market.kospi, data.market.kospi200_futures, ...(data.market.factors || [])];
  $("marketGrid").innerHTML = market.filter(Boolean).map(renderMarketCard).join("") || `<p class="empty">시장 데이터가 없습니다.</p>`;
  $("timeline").innerHTML = (data.timeline || []).map(item => `<div class="timeline-item ${item.status}"><span class="timeline-time">${escapeHtml(item.at)}</span><span class="timeline-dot"></span><div class="timeline-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.note)}</span></div></div>`).join("");

  $("newsList").innerHTML = (data.news || []).length ? data.news.map(item => `<a class="list-item" href="${safeUrl(item.source_url || item.link)}" target="_blank" rel="noopener"><strong><span class="impact ${escapeHtml(item.material_direction || item.impact || "unknown")}"></span>${escapeHtml(item.title)}</strong><span class="list-meta"><span>${escapeHtml(safeText(item.source_name || item.source,"뉴스"))}</span><span>${escapeHtml(safeText(item.date_label,"시간 미제공"))}</span><span>${escapeHtml(safeText(item.material_type,"기타"))} · 출처 ${fmtNum(item.source_count || 1,0)}개</span>${item.official_disclosure ? "<span>공식 공시</span>" : ""}</span></a>`).join("") : `<p class="empty">수집된 기사가 없습니다.</p>`;
  $("eventList").innerHTML = (data.events || []).length ? data.events.slice(0,12).map(item => `<div class="list-item"><strong>${escapeHtml(item.name)}</strong><span class="list-meta"><span>${escapeHtml(item.date)}</span><span>${escapeHtml(item.source)}</span></span></div>`).join("") : `<p class="empty">예정된 FRED 발표를 불러오지 못했습니다.</p>`;

  const promoted = Boolean(data.promotion.signal_enabled);
  $("modelStatus").textContent = promoted ? "승격 통과" : "연구 단계";
  $("modelStatus").className = `badge ${promoted ? "up" : ""}`;
  const v = data.validation;
  $("modelMetrics").innerHTML = `<dt>ROC-AUC</dt><dd>${fmtNum(v.roc_auc,4)}</dd><dt>Brier 개선</dt><dd>${fmtNum(v.brier_improvement,6)}</dd><dt>검증 표본</dt><dd>${fmtNum(v.oos_n,0)}</dd><dt>전략 Sharpe</dt><dd>${fmtNum(v.strategy_sharpe,3)}</dd><dt>최대 낙폭</dt><dd>${fmtPct(v.max_drawdown,1)}</dd>`;
  $("officialBadge").textContent = data.data_quality.target_official ? "KRX 공식" : "임시 데이터 포함";
  $("officialBadge").className = `badge ${data.data_quality.target_official ? "up" : ""}`;
  $("dataMetrics").innerHTML = `<dt>최근 KOSPI</dt><dd>${safeText(data.data_quality.target_date_max)}</dd><dt>최근 출처</dt><dd>${safeText(data.data_quality.latest_source)}</dd><dt>예측 생성</dt><dd>${new Date(data.generated_at_seoul).toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Seoul"})}</dd>`;
  const warnings = data.data_quality.warnings || [];
  $("warningList").innerHTML = warnings.length ? warnings.map(w => `<div>⚠ ${escapeHtml(w)}</div>`).join("") : `<div class="up">수집 경고 없음</div>`;
}

async function loadData(force=false) {
  const button = $("refreshButton");
  const previousGeneratedAt = currentDashboard?.generated_at_seoul;
  button.disabled = true;
  button.classList.add("loading");
  try {
    const response = await fetch(`data/dashboard.json?t=${Date.now()}`, {
      cache:"no-store",
      headers:{"Cache-Control":"no-cache","Pragma":"no-cache"}
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    render(data);
    const remoteVersion = safeText(data?.app_version, "0.0.0");
    const remoteIsNewer = remoteVersion.split(".").map(Number).some((value, index, values) => {
      const current = Number(APP_SHELL_VERSION.split(".")[index] || 0);
      const priorEqual = values.slice(0, index).every((prior, priorIndex) => prior === Number(APP_SHELL_VERSION.split(".")[priorIndex] || 0));
      return priorEqual && value > current;
    });
    if (force) {
      const changed = previousGeneratedAt && previousGeneratedAt !== data.generated_at_seoul;
      showToast(remoteIsNewer ? "새 앱 화면을 확인했습니다. 화면 파일을 다시 받습니다." : (changed ? "새 예측 데이터로 갱신했습니다." : `현재 최신 배포본입니다. ${nextAutoUpdate()}`), true);
    }
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration) await registration.update();
    }
    if (remoteIsNewer) await reloadAppShell();
  } catch (error) {
    if (!currentDashboard && window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__);
    showToast(`최신 데이터 확인 실패: ${error.message}`);
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

async function reloadAppShell() {
  if (appShellReloading) return;
  appShellReloading = true;
  const button = $("screenRefreshButton");
  button.disabled = true;
  button.textContent = "갱신 중";
  try {
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration) {
        await registration.update();
        if (registration.waiting) registration.waiting.postMessage({type:"SKIP_WAITING"});
      }
    }
  } catch (error) {
    console.warn("App shell update check failed", error);
  }
  window.setTimeout(() => window.location.reload(), 500);
}

function activateTab(name, updateHash=true) {
  const selected = ["today","candidates","research"].includes(name) ? name : "today";
  document.querySelectorAll("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== selected; });
  document.querySelectorAll("[data-tab]").forEach(button => {
    const active = button.dataset.tab === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (updateHash && window.location.hash !== `#${selected}`) history.replaceState(null, "", `#${selected}`);
}

document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => activateTab(button.dataset.tab)));
window.addEventListener("hashchange", () => activateTab(window.location.hash.slice(1), false));
activateTab(window.location.hash.slice(1), false);

$("refreshButton").addEventListener("click", () => loadData(true));
$("screenRefreshButton").addEventListener("click", reloadAppShell);
if (window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__);
loadData(false);
setInterval(() => loadData(false), 120000);
setInterval(() => currentDashboard && renderFreshness(currentDashboard), 60000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") loadData(false); });
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  const hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (hadController && !appShellReloading) {
      appShellReloading = true;
      window.location.reload();
    }
  });
  navigator.serviceWorker.register("sw.js", {updateViaCache:"none"});
}
