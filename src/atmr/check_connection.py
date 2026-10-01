"""
Standalone MT5 connection test.

Run this BEFORE anything else:
    python -m atmr.check_connection

It does nothing except: connect to MT5 using the credentials in .env,
print your account info, and print 5 recent EURUSD candles.
If this fails, nothing else in the project will work either.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    print("Run: pip install MetaTrader5")
    sys.exit(1)


def main() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)

    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    if not all([login, password, server]):
        print("ERROR: MT5_LOGIN, MT5_PASSWORD or MT5_SERVER missing from .env")
        print("Copy .env.example to .env and fill in your demo credentials.")
        sys.exit(1)

    print(f"Connecting to MT5 server: {server} ...")

    assert login is not None and password is not None and server is not None
    if not mt5.initialize(login=int(login), password=password, server=server):
        print("FAILED to connect.")
        print("MT5 error:", mt5.last_error())
        print()
        print("Common causes:")
        print("  - MT5 terminal is not installed or not open")
        print("  - 'Allow algorithmic trading' is not enabled in MT5 options")
        print("  - Wrong login/password/server in .env")
        sys.exit(1)

    account = mt5.account_info()
    if account is None:
        print("Connected, but could not read account info.")
        print("MT5 error:", mt5.last_error())
        mt5.shutdown()
        sys.exit(1)

    print()
    print("SUCCESS. Connected to MT5.")
    print(f"  Login:   {account.login}")
    print(f"  Server:  {account.server}")
    print(f"  Balance: {account.balance} {account.currency}")
    print(f"  Equity:  {account.equity}")

    print()
    print("Fetching 5 recent EURUSD H1 candles...")
    rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_H1, 0, 5)

    if rates is None or len(rates) == 0:
        print("Could not fetch candle data.")
        print("MT5 error:", mt5.last_error())
        print("Check that EURUSD is visible in MT5 (Market Watch) and your")
        print("broker's symbol name matches (see docs/DECISIONS.md D8).")
    else:
        for r in rates:
            print(f"  time={r['time']}  open={r['open']}  close={r['close']}")
        print()
        print("All good. MT5 + Python are talking to each other.")

    mt5.shutdown()


if __name__ == "__main__":
    main()
