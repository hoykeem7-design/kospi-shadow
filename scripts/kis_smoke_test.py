from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from kospi_shadow.data import fetch_kis_kospi_recent


def main() -> int:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    frame = fetch_kis_kospi_recent(as_of_date=today, timeout=30, retries=3)
    if frame.empty:
        raise RuntimeError("KIS returned no KOSPI daily-index rows")
    row = frame.iloc[-1]
    print(
        "KIS_SMOKE_OK "
        f"latest_date={row['Date'].strftime('%Y-%m-%d')} "
        f"close={float(row['Close']):.2f} rows={len(frame)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
