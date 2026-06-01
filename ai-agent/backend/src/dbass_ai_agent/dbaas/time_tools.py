from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


LOCAL_TIMEZONE = "Asia/Shanghai"


def get_current_time() -> dict[str, object]:
    now_utc = datetime.now(tz=UTC)
    local = now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    return {
        "status": "success",
        "now_ts": int(now_utc.timestamp()),
        "iso_utc": now_utc.isoformat().replace("+00:00", "Z"),
        "iso_local": local.isoformat(),
        "local_datetime": local.strftime("%Y-%m-%d %H:%M:%S"),
        "local_date": local.strftime("%Y-%m-%d"),
        "timezone": LOCAL_TIMEZONE,
    }
