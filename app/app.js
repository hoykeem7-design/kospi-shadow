const $ = (id) => document.getElementById(id);
const fmtPct = (value, digits=2) => value == null ? "--" : `${(Number(value)*100).toFixed(digits)}%`;
const fmtNum = (value, digits=2) => value == null ? "--" : Number(value).toLocaleString("ko-KR", {maximumFractionDigits: digits});
const clsFor = (value) => value == null || Math.abs(value) < 1e-12 ? "neutral" : (value > 0 ? "up" : "down");
const safeText = (value, fallback="--") => value == null || value === "" ? fallback : String(value);
const escapeHtml = (value) => safeText(value, "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const safeUrl = (value) => { try { const url = new URL(String(value)); return ["http:","https:"].includes(url.protocol) ? url.href : "#"; } catch { return "#"; } };

let deferredPrompt;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); deferredPrompt = event; $("installButton").classList.remove("hidden");
});
$("installButton").addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt(); await deferredPrompt.userChoice; deferredPrompt = null; $("installButton").classList.add("hidden");
});

function renderMarketCard(item) {
  const rate = item?.change_rate;
  return `<div class="market-item"><span class="market-name">${escapeHtml(item?.name)}</span><strong class="market-value">${fmtNum(item?.price)}</strong><span class="market-change ${clsFor(rate)}">${rate == null ? "데이터 없음" : `${rate >= 0 ? "+" : ""}${fmtPct(rate)}`}</span></div>`;
}
function render(data) {
  const p = Number(data.prediction.probability_intraday_up ?? .5);
  $("sessionBadge").textContent = data.session.label;
  $("generatedAt").textContent = new Date(data.generated_at_seoul).toLocaleString("ko-KR", {hour:"2-digit",minute:"2-digit",month:"numeric",day:"numeric",timeZone:"Asia/Seoul"});
  $("sessionTitle").textContent = data.coaching.headline;
  $("sessionDescription").textContent = data.session.description;
  $("probability").textContent = fmtPct(p,1);
  $("gauge").style.setProperty("--p", Math.max(0,Math.min(100,p*100)));
  $("targetDate").textContent = data.prediction.candidate_target_date;
  const direction = safeText(data.prediction.research_direction,"FLAT");
  $("directionBadge").textContent = direction;
  $("directionBadge").className = `direction ${direction.toLowerCase()}`;
  $("coachHeadline").textContent = data.coaching.headline;
  $("coachRationale").textContent = data.coaching.rationale;
  $("timingScore").textContent = `${fmtNum(data.coaching.timing_score,0)}점`;
  $("confidenceLabel").textContent = data.coaching.confidence_label;
  $("nextCheckpoint").textContent = `${data.coaching.next_checkpoint_at} ${data.coaching.next_checkpoint_label}`;
  $("dataSource").textContent = `${safeText(data.data_quality.latest_source)} · ${safeText(data.data_quality.target_date_max)}`;
  $("briefingList").innerHTML = (data.briefing || []).length ? (data.briefing || []).map(item => `<div class="briefing-item"><strong><span class="briefing-mark ${item.tone}"></span>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.text)}</p></div>`).join("") : `<p class="empty">핵심 요약을 생성하지 못했습니다.</p>`;

  const market = [data.market.kospi, data.market.kospi200_futures, ...(data.market.factors || [])];
  $("marketGrid").innerHTML = market.filter(Boolean).map(renderMarketCard).join("") || `<p class="empty">시장 데이터가 없습니다.</p>`;
  $("timeline").innerHTML = (data.timeline || []).map(item => `<div class="timeline-item ${item.status}"><span class="timeline-time">${item.at}</span><span class="timeline-dot"></span><div class="timeline-copy"><strong>${item.label}</strong><span>${item.note}</span></div></div>`).join("");

  $("newsList").innerHTML = (data.news || []).length ? data.news.map(item => `<a class="list-item" href="${safeUrl(item.link)}" target="_blank" rel="noopener"><strong><span class="impact ${item.impact}"></span>${escapeHtml(item.title)}</strong><span class="list-meta"><span>${escapeHtml(safeText(item.source,"뉴스"))}</span><span>${escapeHtml((item.tags||[]).join(" · "))}</span></span></a>`).join("") : `<p class="empty">수집된 기사가 없습니다.</p>`;
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
  try {
    const response = await fetch(`data/dashboard.json${force ? `?t=${Date.now()}` : ""}`, {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json(); render(data);
  } catch (error) {
    if (window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__);
    $("errorToast").textContent = `최신 데이터 갱신 실패: ${error.message}`;
    $("errorToast").classList.remove("hidden"); setTimeout(() => $("errorToast").classList.add("hidden"), 5000);
  }
}
$("refreshButton").addEventListener("click", () => loadData(true));
if (window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__); else loadData();
setInterval(() => loadData(true), 60000);
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("sw.js");
