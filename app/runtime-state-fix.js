(() => {
  "use strict";

  const FIRST_DAILY_PREDICTION_MINUTE = 7 * 60 + 30;
  const AFTERMARKET_END_MINUTE = 20 * 60 + 5;
  const EXPECTED_OVERNIGHT_MAX_AGE_MINUTES = 18 * 60;
  const EXPECTED_WEEKEND_MAX_AGE_MINUTES = 4 * 24 * 60;

  let lastDashboard = null;

  function element(id) {
    return document.getElementById(id);
  }

  function seoulParts(date = new Date()) {
    const values = {};
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    });
    for (const part of formatter.formatToParts(date)) {
      if (part.type !== "literal") values[part.type] = part.value;
    }
    return {
      year: Number(values.year),
      month: Number(values.month),
      day: Number(values.day),
      weekday: values.weekday,
      minute: Number(values.hour) * 60 + Number(values.minute)
    };
  }

  function parseInstant(value) {
    if (!value) return null;
    const instant = new Date(value);
    return Number.isNaN(instant.getTime()) ? null : instant;
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

  function setText(id, text) {
    const node = element(id);
    if (node) node.textContent = text;
  }

  function setFreshness(label, tone, nextLabel) {
    const badge = element("freshnessBadge");
    if (badge) {
      badge.textContent = label;
      badge.className = `badge ${tone}`;
    }
    setText("nextAutoUpdate", nextLabel);
  }

  function setLockedGate(status, action, explanation, label) {
    const statusNode = element("marketGateStatus");
    if (statusNode) {
      statusNode.textContent = status;
      statusNode.className = "gate-status gate-unavailable";
    }
    setText("marketGateAction", action);
    setText("marketGateSummary", explanation);
    setText("marketGateReason", explanation);
    setText("dockGateStatus", status);
    setText("dockAction", action);

    const abstention = element("marketGateAbstention");
    if (abstention) {
      abstention.className = "abstention active";
      const strong = abstention.querySelector("strong");
      if (strong) strong.textContent = label;
    }

    const candidateNotice = element("candidateGateNotice");
    if (candidateNotice) {
      candidateNotice.className = "candidate-gate-notice locked";
      candidateNotice.textContent = `${label} · 종목 후보는 관찰 전용이며 주문 후보가 아닙니다.`;
    }
  }

  function apply(data, now = new Date()) {
    if (!data || typeof data !== "object") return;
    lastDashboard = data;

    const clock = seoulParts(now);
    const isWeekend = ["Sat", "Sun"].includes(clock.weekday);
    const generated = parseInstant(data.generated_at_seoul);
    const snapshotAge = ageMinutes(generated, now);
    const snapshotLabel = formatKst(generated);

    if (isWeekend) {
      const expected = snapshotAge != null && snapshotAge <= EXPECTED_WEEKEND_MAX_AGE_MINUTES;
      setFreshness(
        expected ? `최근 영업일 마감 스냅샷 · ${snapshotLabel}` : `주의 · 최근 스냅샷 ${snapshotLabel}`,
        expected ? "fresh" : "stale",
        "다음 영업일 07:30 예측 준비"
      );
      setLockedGate(
        "CLOSED · 주말·휴장",
        "거래 없음 · 다음 영업일 준비",
        expected
          ? "현재는 휴장 구간입니다. 최근 영업일 마감 데이터는 참고용으로 유지됩니다."
          : "현재는 휴장 구간이며 최근 스냅샷도 오래되었습니다. 다음 영업일 갱신을 확인해야 합니다.",
        "주말·휴장 · 신규 판단 대기"
      );
      return;
    }

    if (clock.minute < FIRST_DAILY_PREDICTION_MINUTE) {
      const expected = snapshotAge != null && snapshotAge <= EXPECTED_OVERNIGHT_MAX_AGE_MINUTES;
      setFreshness(
        expected ? `전일 마감 스냅샷 · ${snapshotLabel}` : `주의 · 전일 스냅샷 오래됨 · ${snapshotLabel}`,
        expected ? "fresh" : "stale",
        "오늘 07:30 예측 준비"
      );
      setLockedGate(
        "PREP · 오늘 예측 준비 중",
        "전일 마감 참고만 · 07:30 이후 판단",
        expected
          ? "현재는 장 시작 전입니다. 전일 마감 데이터는 정상이며 오늘을 대상으로 한 확률은 07:30 체크포인트부터 생성됩니다."
          : "현재는 장 시작 전이지만 전일 스냅샷이 예상보다 오래되었습니다. 07:30 갱신 성공 여부를 먼저 확인해야 합니다.",
        "장 시작 전 · 오늘 판단 대기"
      );
      return;
    }

    if (clock.minute >= AFTERMARKET_END_MINUTE) {
      const expected = snapshotAge != null && snapshotAge <= 180;
      setFreshness(
        expected ? `오늘 마감 스냅샷 · ${snapshotLabel}` : `주의 · 마감 스냅샷 확인 필요 · ${snapshotLabel}`,
        expected ? "fresh" : "stale",
        "다음 영업일 07:30 예측 준비"
      );
      setLockedGate(
        "CLOSED · 오늘 거래 종료",
        "오늘 거래 종료 · 익일 관찰 준비",
        expected
          ? "정규장과 애프터마켓 체크포인트가 종료됐습니다. 현재 데이터는 오늘 마감 검토용입니다."
          : "오늘 거래는 종료됐지만 마감 스냅샷이 예상보다 오래되었습니다. 다음 영업일 전 갱신 상태를 확인해야 합니다.",
        "거래 종료 · 신규 판단 없음"
      );
    }
  }

  function install() {
    const baseRender = typeof window.render === "function" ? window.render : null;
    if (baseRender && !baseRender.__runtimeStateFixWrapped) {
      const wrapped = function wrappedRender(data) {
        const result = baseRender(data);
        queueMicrotask(() => apply(data));
        return result;
      };
      wrapped.__runtimeStateFixWrapped = true;
      window.render = wrapped;
    }

    if (window.__INITIAL_DASHBOARD__) queueMicrotask(() => apply(window.__INITIAL_DASHBOARD__));
    window.setInterval(() => { if (lastDashboard) apply(lastDashboard); }, 60000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && lastDashboard) apply(lastDashboard);
    });
  }

  install();
})();
