(() => {
  "use strict";

  const VERSION = "5.4.0";
  const OFFICIAL_HOST = "hoykeem7-design.github.io";
  const OFFICIAL_PATH = "/kospi-shadow/";
  const OFFICIAL_URL = `https://${OFFICIAL_HOST}${OFFICIAL_PATH}`;
  const MARKET_STALE_AFTER_MINUTES = 15;
  const MODEL_STALE_AFTER_MINUTES = 8 * 24 * 60;
  const ACTIVE_START_MINUTE = 8 * 60;
  const ACTIVE_END_MINUTE = 20 * 60 + 5;

  let lastDashboard = null;
  let redirectScheduled = false;

  function parseInstant(value) {
    if (!value || typeof value !== "string") return null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function ageMinutes(value, now = new Date()) {
    const instant = value instanceof Date ? value : parseInstant(value);
    if (!instant) return null;
    return Math.max(0, Math.floor((now.getTime() - instant.getTime()) / 60000));
  }

  function formatKst(value) {
    const instant = value instanceof Date ? value : parseInstant(value);
    if (!instant) return "시각 미제공";
    return instant.toLocaleString("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function formatAge(age) {
    if (age == null) return "경과시간 불명";
    if (age < 60) return `${age}분 전`;
    const hours = Math.floor(age / 60);
    const minutes = age % 60;
    return minutes ? `${hours}시간 ${minutes}분 전` : `${hours}시간 전`;
  }

  function seoulClock(now = new Date()) {
    const parts = {};
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    });
    for (const part of formatter.formatToParts(now)) {
      if (part.type !== "literal") parts[part.type] = part.value;
    }
    return {
      weekday: parts.weekday,
      minute: Number(parts.hour) * 60 + Number(parts.minute)
    };
  }

  function activeMarketSession(now = new Date()) {
    const clock = seoulClock(now);
    if (["Sat", "Sun"].includes(clock.weekday)) return false;
    return clock.minute >= ACTIVE_START_MINUTE && clock.minute <= ACTIVE_END_MINUTE;
  }

  function latestNewsInstant(data) {
    const candidates = (data?.news || [])
      .flatMap((item) => [item?.received_at, item?.published_at_kst, item?.published_at])
      .map(parseInstant)
      .filter(Boolean)
      .sort((left, right) => right.getTime() - left.getTime());
    return candidates[0] || null;
  }

  function modelInstant(data) {
    return parseInstant(data?.prediction?.trained_at_utc)
      || parseInstant(data?.market_refresh?.model_snapshot_source_generated_at_seoul)
      || parseInstant(data?.prediction?.generated_at_seoul)
      || null;
  }

  function marketState(data, now = new Date()) {
    const index = data?.market?.kospi || null;
    const futures = data?.market?.kospi200_futures || null;
    const indexInstant = parseInstant(index?.received_at || index?.observed_at);
    const futuresInstant = parseInstant(futures?.received_at || futures?.observed_at);
    const ages = [indexInstant, futuresInstant].filter(Boolean).map((instant) => ageMinutes(instant, now));
    const oldestAge = ages.length ? Math.max(...ages) : null;
    const newestInstant = [indexInstant, futuresInstant]
      .filter(Boolean)
      .sort((left, right) => right.getTime() - left.getTime())[0] || null;
    const complete = Boolean(indexInstant && futuresInstant);
    const active = activeMarketSession(now);
    const fresh = complete && oldestAge != null && oldestAge <= MARKET_STALE_AFTER_MINUTES;
    return {
      active,
      complete,
      fresh,
      oldestAge,
      newestInstant,
      locked: active && !fresh
    };
  }

  function ensureStyles() {
    if (document.getElementById("operationalTrustStyles")) return;
    const style = document.createElement("style");
    style.id = "operationalTrustStyles";
    style.textContent = `
      .operational-trust-panel{margin-top:16px;padding:16px;border:1px solid rgba(124,166,255,.28);border-radius:18px;background:rgba(7,15,31,.52)}
      .operational-trust-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
      .operational-trust-head strong{font-size:14px;letter-spacing:.02em}
      .operational-trust-host{font-size:12px;color:#9fb1cc}
      .operational-trust-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
      .operational-trust-item{padding:12px;border-radius:14px;background:rgba(13,27,51,.72);border:1px solid rgba(125,158,207,.18)}
      .operational-trust-item span{display:block;font-size:11px;color:#91a5c4;margin-bottom:5px}
      .operational-trust-item strong{display:block;font-size:14px;line-height:1.35}
      .operational-trust-item small{display:block;margin-top:4px;color:#8ea1bd;font-size:11px;line-height:1.35;word-break:break-word}
      .operational-trust-item.ok{border-color:rgba(60,202,151,.38)}
      .operational-trust-item.warn{border-color:rgba(255,190,92,.48);background:rgba(67,49,17,.35)}
      .operational-trust-item.danger{border-color:rgba(255,103,111,.58);background:rgba(75,22,31,.46)}
      .operational-lock-banner{display:none;margin:0 0 16px;padding:15px 16px;border-radius:16px;border:1px solid rgba(255,103,111,.58);background:rgba(92,25,35,.92);box-shadow:0 12px 30px rgba(0,0,0,.24)}
      .operational-lock-banner.active{display:block}
      .operational-lock-banner strong{display:block;font-size:15px;margin-bottom:5px}
      .operational-lock-banner span{display:block;font-size:13px;line-height:1.45;color:#ffd9dc}
      .operational-lock-banner a{display:inline-flex;margin-top:10px;padding:8px 12px;border-radius:10px;background:#fff;color:#172033;font-weight:700;text-decoration:none}
      body.operational-trade-locked .market-gate{border-color:rgba(255,103,111,.48)}
      body.operational-trade-locked #marketGateAction{color:#ffd7da}
      @media(max-width:520px){.operational-trust-grid{grid-template-columns:1fr}.operational-trust-panel{padding:14px}}
    `;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    let panel = document.getElementById("operationalTrustPanel");
    if (panel) return panel;
    const anchor = document.querySelector("#kospi-market-gate .cockpit-meta");
    if (!anchor) return null;
    panel = document.createElement("section");
    panel.id = "operationalTrustPanel";
    panel.className = "operational-trust-panel";
    panel.setAttribute("aria-label", "운영 신뢰도 상태");
    panel.innerHTML = `
      <div class="operational-trust-head">
        <strong>운영 신뢰도</strong>
        <span id="operationalTrustHost" class="operational-trust-host"></span>
      </div>
      <div class="operational-trust-grid">
        <div id="trustMarket" class="operational-trust-item"><span>시장 데이터</span><strong>확인 중</strong><small></small></div>
        <div id="trustModel" class="operational-trust-item"><span>모델 확률</span><strong>확인 중</strong><small></small></div>
        <div id="trustNews" class="operational-trust-item"><span>뉴스·공시</span><strong>확인 중</strong><small></small></div>
        <div id="trustApp" class="operational-trust-item"><span>앱·배포</span><strong>확인 중</strong><small></small></div>
      </div>`;
    anchor.insertAdjacentElement("afterend", panel);
    return panel;
  }

  function ensureLockBanner() {
    let banner = document.getElementById("operationalLockBanner");
    if (banner) return banner;
    const gate = document.getElementById("kospi-market-gate");
    if (!gate) return null;
    banner = document.createElement("div");
    banner.id = "operationalLockBanner";
    banner.className = "operational-lock-banner";
    banner.innerHTML = `<strong id="operationalLockTitle"></strong><span id="operationalLockText"></span><a id="operationalMigrationLink" href="${OFFICIAL_URL}">최신 공식 앱 열기</a>`;
    gate.insertAdjacentElement("beforebegin", banner);
    return banner;
  }

  function setTrustItem(id, tone, title, detail) {
    const node = document.getElementById(id);
    if (!node) return;
    node.className = `operational-trust-item ${tone}`;
    const strong = node.querySelector("strong");
    const small = node.querySelector("small");
    if (strong) strong.textContent = title;
    if (small) small.textContent = detail;
  }

  function officialLocation() {
    const local = ["localhost", "127.0.0.1"].includes(location.hostname);
    if (local) return {official: true, local: true};
    return {
      official: location.hostname === OFFICIAL_HOST && location.pathname.startsWith(OFFICIAL_PATH),
      local: false
    };
  }

  function applyOperationalLock(data, state) {
    const banner = ensureLockBanner();
    if (!banner) return;
    const locationState = officialLocation();
    const modelDate = modelInstant(data);
    const modelAge = ageMinutes(modelDate);
    const modelUnsafe = modelDate == null || modelAge > MODEL_STALE_AFTER_MINUTES;
    const wrongHost = !locationState.official;
    const locked = wrongHost || state.locked || (state.active && modelUnsafe);
    document.body.classList.toggle("operational-trade-locked", locked);
    banner.classList.toggle("active", locked);
    const migrationLink = document.getElementById("operationalMigrationLink");
    if (migrationLink) migrationLink.style.display = wrongHost ? "inline-flex" : "none";

    if (!locked) return;
    let title = "매매 판단 잠금";
    let text = "필수 운영 조건이 충족되지 않아 신규 매매 판단을 차단했습니다.";
    if (wrongHost) {
      title = "구주소·비공식 배포 감지";
      text = "이 주소는 최신 운영 앱이 아닙니다. GitHub Pages 공식 앱으로 이동합니다.";
    } else if (state.locked) {
      title = "시장 데이터 지연 · 매매 판단 잠금";
      text = `장중 KOSPI 현물·선물 데이터가 ${MARKET_STALE_AFTER_MINUTES}분 이내로 갱신되지 않았습니다.`;
    } else if (modelUnsafe) {
      title = "모델 스냅샷 만료 · 매매 판단 잠금";
      text = "검증 모델의 생성 시각이 없거나 허용 기간을 초과했습니다.";
    }
    const titleNode = document.getElementById("operationalLockTitle");
    const textNode = document.getElementById("operationalLockText");
    if (titleNode) titleNode.textContent = title;
    if (textNode) textNode.textContent = text;

    const status = document.getElementById("marketGateStatus");
    const action = document.getElementById("marketGateAction");
    const summary = document.getElementById("marketGateSummary");
    const abstention = document.getElementById("marketGateAbstention");
    const reason = document.getElementById("marketGateReason");
    const dockStatus = document.getElementById("dockGateStatus");
    const dockAction = document.getElementById("dockAction");
    if (status) {
      status.textContent = "DATA_STALE · 판단 잠금";
      status.className = "gate-status gate-unavailable";
    }
    if (action) action.textContent = "매매 보류 · 데이터 갱신 필요";
    if (summary) summary.textContent = text;
    if (abstention) abstention.className = "abstention active";
    if (reason) reason.textContent = text;
    if (dockStatus) dockStatus.textContent = "DATA_STALE · 판단 잠금";
    if (dockAction) dockAction.textContent = "매매 보류 · 데이터 갱신 필요";
    const candidateNotice = document.getElementById("candidateGateNotice");
    if (candidateNotice) {
      candidateNotice.className = "candidate-gate-notice locked";
      candidateNotice.textContent = `${title} · 모든 종목은 관찰 전용이며 주문 후보가 아닙니다.`;
    }
  }

  function apply(data) {
    if (!data || typeof data !== "object") return;
    lastDashboard = data;
    ensureStyles();
    ensurePanel();
    const now = new Date();
    const market = marketState(data, now);
    const modelDate = modelInstant(data);
    const modelAge = ageMinutes(modelDate, now);
    const newsDate = latestNewsInstant(data);
    const newsAge = ageMinutes(newsDate, now);
    const locationState = officialLocation();
    const reused = Boolean(data?.market_refresh?.model_snapshot_reused);
    const buildSha = String(data?.build_sha || data?.market_refresh?.source_sha || "미제공");

    const marketTone = market.active ? (market.fresh ? "ok" : "danger") : (market.complete ? "ok" : "warn");
    const marketTitle = market.active
      ? (market.fresh ? "장중 정상" : "장중 지연")
      : (market.complete ? "마감·비활성 스냅샷" : "일부 미수신");
    setTrustItem(
      "trustMarket",
      marketTone,
      marketTitle,
      `${formatKst(market.newestInstant)} · ${formatAge(market.oldestAge)}${market.active ? ` · 허용 ${MARKET_STALE_AFTER_MINUTES}분` : ""}`
    );

    const modelExpired = modelAge == null || modelAge > MODEL_STALE_AFTER_MINUTES;
    setTrustItem(
      "trustModel",
      modelExpired ? "danger" : (reused ? "warn" : "ok"),
      modelExpired ? "모델 만료·시각 불명" : (reused ? "검증 스냅샷 재사용" : "모델 최신"),
      `${formatKst(modelDate)} · ${formatAge(modelAge)}`
    );

    setTrustItem(
      "trustNews",
      newsAge == null ? "warn" : (newsAge <= 120 ? "ok" : "warn"),
      newsAge == null ? "시각 미수신" : (newsAge <= 120 ? "최근 자료" : "업데이트 확인 필요"),
      `${formatKst(newsDate)} · ${formatAge(newsAge)}`
    );

    setTrustItem(
      "trustApp",
      locationState.official ? "ok" : "danger",
      locationState.local ? `로컬 · v${VERSION}` : (locationState.official ? `GitHub Pages · v${VERSION}` : "구주소·비공식 배포"),
      `build ${buildSha.slice(0, 10)} · ${location.hostname}`
    );

    const host = document.getElementById("operationalTrustHost");
    if (host) host.textContent = locationState.official ? "공식 운영 주소" : "주소 확인 필요";
    document.querySelectorAll(".version-pill").forEach((node) => { node.textContent = `v${VERSION}`; });
    const versionBadge = document.getElementById("versionBadge");
    if (versionBadge) versionBadge.textContent = `v${VERSION}`;
    document.title = `KOSPI Shadow Decision Coach v${VERSION}`;

    applyOperationalLock(data, market);

    if (!locationState.official && !locationState.local && !redirectScheduled) {
      redirectScheduled = true;
      window.setTimeout(() => {
        const suffix = location.hash || "";
        location.replace(`${OFFICIAL_URL}${suffix}`);
      }, 1400);
    }
  }

  function install() {
    ensureStyles();
    const baseRender = typeof window.render === "function" ? window.render : null;
    if (baseRender && !baseRender.__operationalTrustWrapped) {
      const wrapped = function wrappedRender(data) {
        const result = baseRender(data);
        queueMicrotask(() => apply(data));
        return result;
      };
      wrapped.__operationalTrustWrapped = true;
      window.render = wrapped;
    }
    if (window.__INITIAL_DASHBOARD__) apply(window.__INITIAL_DASHBOARD__);
    window.setInterval(() => { if (lastDashboard) apply(lastDashboard); }, 60000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && lastDashboard) apply(lastDashboard);
    });
  }

  install();
})();
