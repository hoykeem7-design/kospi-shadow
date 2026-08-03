const $ = (id) => document.getElementById(id);
const fmtPct = (value, digits=2) => value == null ? "--" : `${(Number(value)*100).toFixed(digits)}%`;
const fmtNum = (value, digits=2) => value == null ? "--" : Number(value).toLocaleString("ko-KR", {maximumFractionDigits: digits});
const clsFor = (value) => value == null || Math.abs(value) < 1e-12 ? "neutral" : (value > 0 ? "up" : "down");
const safeText = (value, fallback="--") => value == null || value === "" ? fallback : String(value);
const escapeHtml = (value) => safeText(value, "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const safeUrl = (value) => { try { const url = new URL(String(value)); return ["http:","https:"].includes(url.protocol) ? url.href : "#"; } catch { return "#"; } };
const AUTO_UPDATE_TIMES = ["07:45","08:10","08:47","09:10","12:00","15:20","15:35","20:05"];

let deferredPrompt;
let currentDashboard = null;

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
  if (minute < 360) return {label:"야간 선물", description:"KOSPI200 야간선물과 미국장을 반영하는 구간입니다."};
  if (minute < 480) return {label:"장전 준비", description:"전일 국장·미국장·야간선물·뉴스를 합쳐 오늘 계획을 점검합니다."};
  if (minute < 525) return {label:"NXT 프리마켓", description:"08:00 초기 반응을 보되 선물과 본장 확인 전 추격을 피합니다."};
  if (minute < 540) return {label:"선물 개장·본장 직전", description:"KOSPI200 선물 방향과 09:00 현물 개장을 교차 확인합니다."};
  if (minute < 550) return {label:"본장 가격발견", description:"개장 직후 변동성이 커서 첫 10분 확인을 우선합니다."};
  if (minute < 720) return {label:"오전 본장", description:"프리마켓·선물·현물 방향의 일치 여부를 감시합니다."};
  if (minute < 920) return {label:"오후 본장", description:"오전 추세 지속과 선물 변화를 재평가합니다."};
  if (minute < 930) return {label:"마감 직전", description:"신규 추격보다 종가 위험과 익일 보유 여부를 점검합니다."};
  if (minute < 1080) return {label:"NXT 애프터마켓", description:"정규장 결과를 확인하고 다음 영업일 시나리오를 준비합니다."};
  if (minute < 1200) return {label:"애프터마켓·야간선물", description:"애프터와 야간선물의 방향 일치를 확인합니다."};
  return {label:"장 종료 후", description:"오늘 국장과 애프터·야간선물 초기 흐름으로 다음 장을 준비합니다."};
}

function seoulDateToInstant(year, month, day, hour, minute) {
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute));
}

function nextAutoUpdate(now=new Date()) {
  const p = seoulParts(now);
  for (let offset=0; offset<8; offset += 1) {
    const noon = seoulDateToInstant(p.year, p.month, p.day + offset, 12, 0);
    const d = seoulParts(noon);
    if (["Sat","Sun"].includes(d.weekday)) continue;
    for (const at of AUTO_UPDATE_TIMES) {
      const [hour, minute] = at.split(":").map(Number);
      const instant = seoulDateToInstant(d.year, d.month, d.day, hour, minute);
      if (instant.getTime() > now.getTime() + 30000) {
        const dayLabel = offset === 0 ? "오늘" : (offset === 1 ? "내일" : `${d.month}/${d.day}`);
        return `${dayLabel} ${at} 예정`;
      }
    }
  }
  return "다음 영업일 07:45 예정";
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

function renderFreshness(data) {
  const generated = new Date(data.generated_at_seoul);
  const ageMinutes = Math.max(0, Math.floor((Date.now() - generated.getTime()) / 60000));
  const fresh = ageMinutes <= 90;
  $("freshnessBadge").textContent = fresh ? `최신 · ${ageMinutes}분 전` : `주의 · ${ageMinutes}분 전 데이터`;
  $("freshnessBadge").className = `badge ${fresh ? "fresh" : "stale"}`;
  $("nextAutoUpdate").textContent = `다음 자동 업데이트 ${nextAutoUpdate()}`;
}

function render(data) {
  currentDashboard = data;
  const session = runtimeSession();
  const p = Number(data.prediction.probability_intraday_up ?? .5);
  $("sessionBadge").textContent = session.label;
  $("generatedAt").textContent = new Date(data.generated_at_seoul).toLocaleString("ko-KR", {hour:"2-digit",minute:"2-digit",month:"numeric",day:"numeric",timeZone:"Asia/Seoul"});
  $("sessionTitle").textContent = data.coaching.headline;
  $("sessionDescription").textContent = session.description;
  renderFreshness(data);
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
  $("timeline").innerHTML = (data.timeline || []).map(item => `<div class="timeline-item ${item.status}"><span class="timeline-time">${escapeHtml(item.at)}</span><span class="timeline-dot"></span><div class="timeline-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.note)}</span></div></div>`).join("");

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
    if (force) {
      const changed = previousGeneratedAt && previousGeneratedAt !== data.generated_at_seoul;
      showToast(changed ? "새 예측 데이터로 갱신했습니다." : `현재 최신 배포본입니다. ${nextAutoUpdate()}`, true);
    }
    if ("serviceWorker" in navigator) {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration) await registration.update();
    }
  } catch (error) {
    if (!currentDashboard && window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__);
    showToast(`최신 데이터 확인 실패: ${error.message}`);
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

$("refreshButton").addEventListener("click", () => loadData(true));
if (window.__INITIAL_DASHBOARD__) render(window.__INITIAL_DASHBOARD__);
loadData(false);
setInterval(() => loadData(false), 120000);
setInterval(() => currentDashboard && renderFreshness(currentDashboard), 60000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") loadData(false); });
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) navigator.serviceWorker.register("sw.js", {updateViaCache:"none"});
