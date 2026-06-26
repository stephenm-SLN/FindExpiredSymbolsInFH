#!/usr/bin/env python3
"""
Check if a symbol has been delisted from a cryptocurrency exchange using ccxt.
"""

import argparse
import sys
import ccxt


class MarketLoadError(Exception):
    """Raised by load_exchange_markets_safe instead of sys.exit."""


def load_exchange_markets_safe(exchange_id: str) -> tuple[ccxt.Exchange, dict]:
    """
    Library-safe variant of load_exchange_markets.

    Raises MarketLoadError instead of calling sys.exit, so callers can decide
    how to react. The existing CLI (load_exchange_markets) still calls sys.exit.
    """
    if exchange_id not in ccxt.exchanges:
        raise MarketLoadError(f"'{exchange_id}' is not a supported exchange.")
    exchange = getattr(ccxt, exchange_id)()
    try:
        markets = exchange.load_markets()
    except (ccxt.NetworkError, ccxt.ExchangeError) as e:
        raise MarketLoadError(str(e)) from e
    return exchange, markets


def classify(markets: dict, symbol: str) -> tuple[str, str]:
    """
    Pure classification: given a markets dict and a symbol, return (status, detail).
    Status is one of "LISTED", "INACTIVE", "DELISTED".
    """
    sym = symbol.upper()
    if sym not in markets:
        return "DELISTED", ""
    if markets[sym].get("active", True) is False:
        return "INACTIVE", "market.active is False"
    return "LISTED", ""


def check_symbol(exchange_id: str, symbol: str, verbose: bool = False) -> bool:
    """
    Check if a symbol is currently listed on an exchange.

    Returns True if listed, False if delisted/not found.
    """
    exchange, markets = load_exchange_markets(exchange_id, verbose)

    if verbose:
        print(f"Total symbols listed on {exchange.name}: {len(markets)}")

    status, _ = classify(markets, symbol)
    sym_u = symbol.upper()

    if status == "LISTED":
        print(f"LISTED    {sym_u} is actively listed on {exchange.name}.")
        return True
    if status == "INACTIVE":
        print(f"INACTIVE  {sym_u} exists on {exchange.name} but is marked inactive (likely delisted).")
        return False

    base = sym_u.split("/")[0] if "/" in sym_u else sym_u
    matches = [s for s in markets if base in s]
    print(f"DELISTED  {sym_u} was not found on {exchange.name}.")
    if matches:
        print(f"          Similar symbols: {', '.join(matches[:10])}")
    return False


def check_multiple(exchange_id: str, symbols: list[str], verbose: bool = False):
    """Check multiple symbols against an exchange in a single market load."""
    exchange, markets = load_exchange_markets(exchange_id, verbose)

    print(f"\nResults for {exchange.name} ({len(markets)} total markets):\n")
    results = {}
    for symbol in symbols:
        status, _ = classify(markets, symbol)
        sym_u = symbol.upper()
        results[sym_u] = status
        print(f"  {status:<10} {sym_u}")

    return results


def load_exchange_markets(exchange_id: str, verbose: bool = False):
    """Load markets for an exchange, with error handling. Returns (exchange, markets).

    CLI wrapper around load_exchange_markets_safe — prints and sys.exit on failure.
    """
    if verbose:
        print(f"Fetching markets from {exchange_id}...")
    try:
        return load_exchange_markets_safe(exchange_id)
    except MarketLoadError as e:
        msg = str(e)
        if "is not a supported exchange" in msg:
            print(f"Error: {msg}")
        else:
            print(f"Network/exchange error: {msg}")
            if "huobi" in exchange_id or "htx" in exchange_id:
                print("Note: HTX (formerly Huobi) is geo-restricted in some regions (US, EU). Try a VPN or proxy.")
        sys.exit(1)


def list_symbols(exchange_id: str, verbose: bool = False) -> dict[str, list[str]]:
    """
    Return two lists of active symbols for an exchange: spot and perps (linear/inverse swaps & futures).

    Usage:
        result = list_symbols("binance")
        result["spot"]   # e.g. ['BTC/USDT', 'ETH/USDT', ...]
        result["perps"]  # e.g. ['BTC/USDT:USDT', 'ETH/USDT:USDT', ...]
    """
    exchange, markets = load_exchange_markets(exchange_id, verbose)

    spot, perps = [], []
    for symbol, market in markets.items():
        if not market.get("active", True):
            continue
        mtype = market.get("type", "")
        if mtype == "spot":
            spot.append(symbol)
        elif mtype in ("swap", "future") or market.get("linear") or market.get("inverse"):
            perps.append(symbol)

    spot.sort()
    perps.sort()

    print(f"\n{exchange.name} — active spot symbols ({len(spot)}):")
    for s in spot:
        print(f"  {s}")

    print(f"\n{exchange.name} — active perp/futures symbols ({len(perps)}):")
    for s in perps:
        print(f"  {s}")

    return {"spot": spot, "perps": perps}


def main():
    parser = argparse.ArgumentParser(
        description="Check if a symbol is listed or delisted on a ccxt exchange.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python check_delisted_symbol.py binance BTC/USDT
  python check_delisted_symbol.py coinbase ETH/USD -v
  python check_delisted_symbol.py kraken BTC/USD ETH/USD SOL/USD
  python check_delisted_symbol.py binance --list-symbols
  python check_delisted_symbol.py --list-exchanges
        """,
    )
    parser.add_argument("exchange", nargs="?", help="Exchange ID (e.g. binance, coinbase, kraken)")
    parser.add_argument("symbols", nargs="*", help="Symbol(s) to check (e.g. BTC/USDT ETH/USDT)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show extra info")
    parser.add_argument("--list-exchanges", action="store_true", help="List all supported exchanges")
    parser.add_argument("--list-symbols", action="store_true", help="List all active spot and perp symbols on the exchange")

    args = parser.parse_args()

    if args.list_exchanges:
        print("Supported exchanges:")
        for ex in sorted(ccxt.exchanges):
            print(f"  {ex}")
        return

    if args.list_symbols:
        if not args.exchange:
            print("Error: provide an exchange ID with --list-symbols (e.g. binance --list-symbols)")
            sys.exit(1)
        list_symbols(args.exchange, verbose=args.verbose)
        return

    if not args.exchange or not args.symbols:
        parser.print_help()
        sys.exit(1)

    if len(args.symbols) == 1:
        listed = check_symbol(args.exchange, args.symbols[0], verbose=args.verbose)
        sys.exit(0 if listed else 1)
    else:
        results = check_multiple(args.exchange, args.symbols, verbose=args.verbose)
        any_delisted = any(v != "LISTED" for v in results.values())
        sys.exit(1 if any_delisted else 0)


if __name__ == "__main__":
    main()
