from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
TARGET_NAMES = {"코스피", "KOSPI"}


def main() -> int:
    key = os.getenv("KRX_AUTH_KEY", "").strip() or os.getenv("KRX_API_KEY", "").strip()
    if not key:
        print("ERROR: KRX_AUTH_KEY repository secret is missing.")
        return 2

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    errors: list[str] = []
    for offset in range(1, 15):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        date_key = d.strftime("%Y%m%d")
        try:
            r = requests.get(
                ENDPOINT,
                params={"basDd": date_key},
                headers={"AUTH_KEY": key},
                timeout=30,
            )
            if r.status_code != 200:
                errors.append(f"{date_key}: HTTP {r.status_code}")
                continue
            payload = r.json()
            rows = payload.get("OutBlock_1")
            if not isinstance(rows, list):
                message = str(payload)[:240]
                errors.append(f"{date_key}: response has no OutBlock_1 list: {message}")
                continue
            if not rows:
                errors.append(f"{date_key}: empty result (holiday or service not ready)")
                continue
            selected = [r for r in rows if str(r.get("IDX_NM", "")).strip().upper() in TARGET_NAMES]
            if not selected:
                sample_names = [str(x.get("IDX_NM", "")) for x in rows[:10]]
                errors.append(f"{date_key}: KOSPI row not found; sample IDX_NM={sample_names}")
                continue
            row = selected[0]
            print("KRX_SMOKE_OK")
            print(f"date={date_key}")
            print(f"index={row.get('IDX_NM')}")
            print(f"close={row.get('CLSPRC_IDX')}")
            print(f"rows={len(rows)}")
            return 0
        except Exception as exc:
            errors.append(f"{date_key}: {type(exc).__name__}: {exc}")

    print("KRX_SMOKE_FAILED")
    for item in errors[-8:]:
        print(item)
    print("Check that the KOSPI series daily-price API utilization request is approved for this key.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
