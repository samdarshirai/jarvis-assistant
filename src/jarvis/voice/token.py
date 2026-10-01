import argparse
import sys

from jarvis.config import get_settings
from jarvis.db import init_schema, make_pool
from jarvis.voice.devices import Devices


def run(argv: list[str], devices: Devices) -> str:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice.token")
    p.add_argument("--revoke", type=int, metavar="ID", help="delete a device")
    a = p.parse_args(argv)
    if a.revoke is not None:
        return f"Revoked device {a.revoke}." if devices.revoke(a.revoke) else f"No device {a.revoke}."
    did, token = devices.create()
    return f"Device {did} created. Token (shown once, enter it in the app):\n{token}"


def main() -> None:
    pool = make_pool(get_settings().database_url)
    try:
        init_schema(pool)
        print(run(sys.argv[1:], Devices(pool)))
    finally:
        pool.close()


if __name__ == "__main__":
    main()
