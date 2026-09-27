"""Clock fallback: reads the time from a paired device's Bluetooth Current Time Service."""

import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger("bt_clock")

CTS_CHAR_UUID = "00002a2b-0000-1000-8000-00805f9b34fb"


def _parse_cts_payload(data: bytes) -> datetime:
    if len(data) < 7:
        raise ValueError(f"CTS payload too short: {len(data)} bytes")
    year = data[0] | (data[1] << 8)
    month, day, hour, minute, second = data[2], data[3], data[4], data[5], data[6]
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


async def _read_time_async(mac: str, timeout: float) -> datetime:
    from bleak import BleakClient

    async with BleakClient(mac, timeout=timeout) as client:
        raw = await client.read_gatt_char(CTS_CHAR_UUID)
        return _parse_cts_payload(bytes(raw))


def fetch_time_from_bt(mac: str, timeout_sec: float = 15.0):
    """Returns a datetime if the read succeeded, None otherwise."""
    try:
        return asyncio.run(_read_time_async(mac, timeout_sec))
    except Exception:
        log.exception("Failed to fetch time via Bluetooth (%s)", mac)
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: bt_clock.py <MAC> [timeout_sec]", file=sys.stderr)
        sys.exit(2)

    mac_arg = sys.argv[1]
    timeout_arg = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    result = fetch_time_from_bt(mac_arg, timeout_arg)
    if result is None:
        print("FAILED", file=sys.stderr)
        sys.exit(1)
    print(result.astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    sys.exit(0)
