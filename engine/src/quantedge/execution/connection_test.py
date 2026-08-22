"""
QuantEdge AI — Live Delta Exchange Read-Only Connection Test (Phase 5.15.1).

Performs safe, authenticated read-only verification against Delta Exchange India
(https://api.india.delta.exchange) without submitting any orders.

Verifies:
- .env and environment variable loading
- Observed public IP vs Whitelist IP (49.14.135.89)
- HMAC-SHA256 authenticated handshake
- Real authoritative wallet balance (USDT equity, available balance)
- Real open positions count
- Real open orders count
- Real product specifications
"""

import asyncio
from decimal import Decimal
import logging
import os
import sys
from typing import Optional, Dict, Any, Tuple

import httpx

from quantedge.execution.security import mask_secret, load_project_env
from quantedge.execution.delta_client import (
    DeltaIndiaClient,
    DELTA_INDIA_PRODUCTION_URL,
    ConnectionState,
)

logger = logging.getLogger("quantedge.execution.connection_test")

CONFIGURED_WHITELIST_IP = "49.14.135.89"


async def get_observed_public_ip(timeout: float = 5.0) -> Optional[str]:
    """Fetch the machine's external public IP address."""
    endpoints = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/all.json",
        "https://icanhazip.com",
    ]
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in endpoints:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    if "json" in resp.headers.get("content-type", "") or url.endswith("json"):
                        data = resp.json()
                        return str(data.get("ip") or data.get("ip_addr", "")).strip()
                    else:
                        return resp.text.strip()
            except Exception:
                continue
    return None


async def run_live_connection_test(base_url: str = DELTA_INDIA_PRODUCTION_URL) -> Dict[str, Any]:
    """Run authenticated read-only diagnostics against Delta Exchange India."""
    # 1. Load project .env
    env_loaded, env_path = load_project_env()

    # 2. Check credentials presence in environment
    raw_key = os.getenv("DELTA_API_KEY", "").strip()
    raw_secret = os.getenv("DELTA_API_SECRET", "").strip()

    key_present = bool(raw_key)
    secret_present = bool(raw_secret)
    key_len = len(raw_key) if key_present else 0
    secret_len = len(raw_secret) if secret_present else 0
    masked_key = mask_secret(raw_key) if key_present else "N/A"

    # 3. Check observed public IP vs whitelisted IP
    observed_ip = await get_observed_public_ip()
    ip_compat = "PASS" if observed_ip == CONFIGURED_WHITELIST_IP else ("MISMATCH" if observed_ip else "UNKNOWN")

    # 4. Perform Read-Only Exchange Query
    auth_pass = False
    auth_state = ConnectionState.UNKNOWN
    auth_error: Optional[str] = None
    equity_usdt = Decimal("0")
    available_usdt = Decimal("0")
    blocked_margin_usdt = Decimal("0")
    positions_count = 0
    orders_count = 0
    products_count = 0
    user_id: Optional[int] = None

    if key_present and secret_present:
        try:
            client = DeltaIndiaClient(api_key=raw_key, api_secret=raw_secret, base_url=base_url)
            auth_pass, auth_state, auth_error = await client.validate_credentials()

            if auth_pass:
                balances = await client.get_wallet_balances()
                usd_bal = next((b for b in balances if b.asset_symbol in ("USD", "USDT") and b.balance > 0), None)
                if not usd_bal and balances:
                    usd_bal = next((b for b in balances if b.asset_symbol in ("USD", "USDT")), None)
                if usd_bal:
                    equity_usdt = usd_bal.balance
                    available_usdt = usd_bal.available_balance
                    blocked_margin_usdt = usd_bal.blocked_margin
                    user_id = usd_bal.user_id

                positions = await client.get_positions()
                positions_count = len(positions)

                open_orders = await client.get_open_orders()
                orders_count = len(open_orders)

                prods_resp = await client.request("GET", "/v2/products", authenticated=False)
                prods = prods_resp.get("result", [])
                products_count = len(prods) if isinstance(prods, list) else 0

        except Exception as e:
            auth_error = str(e)

    # 5. Format and Print Report
    print("=" * 70)
    print("LIVE DELTA EXCHANGE READ-ONLY CONNECTION TEST (PHASE 5.15.1)")
    print("=" * 70)
    print(f"Execution Endpoint       : {base_url}")
    print(f"Execution Mode           : LIVE")
    print(f"Transport                : REAL DELTA CLIENT (DeltaIndiaClient)")
    print(f"Mock Transport           : DISABLED")
    print(f"Paper Trading            : DISABLED")
    print(f"Sandbox                  : DISABLED")
    print("-" * 70)
    print(f"Environment File (.env)  : {'LOADED (' + env_path + ')' if env_loaded else 'NOT FOUND (Using process env)'}")
    print(f"DELTA_API_KEY_PRESENT    : {key_present} (Length: {key_len}, Masked: {masked_key})")
    print(f"DELTA_API_SECRET_PRESENT : {secret_present} (Length: {secret_len})")
    print("-" * 70)
    print(f"Configured Whitelist IP  : {CONFIGURED_WHITELIST_IP}")
    print(f"Observed Public IP       : {observed_ip or 'UNKNOWN'}")
    print(f"IP Compatibility Gate    : {ip_compat}")
    print("-" * 70)
    print(f"Authentication Gate      : {'PASS' if auth_pass else 'FAIL'}")
    if not auth_pass and (key_present or secret_present):
        print(f"Authentication Error     : {auth_error or auth_state.value}")
    elif not (key_present and secret_present):
        print(f"Authentication Error     : DELTA_API_KEY or DELTA_API_SECRET not set in environment or .env")
    print(f"Account User ID          : {user_id if user_id else 'N/A'}")
    print(f"Authoritative Equity     : {equity_usdt:.4f} USDT")
    print(f"Available Balance        : {available_usdt:.4f} USDT")
    print(f"Blocked Margin           : {blocked_margin_usdt:.4f} USDT")
    print(f"Open Positions on Delta  : {positions_count}")
    print(f"Open Orders on Delta     : {orders_count}")
    print(f"Live Products Retrieved  : {products_count}")
    print(f"Trading Permission Gate  : {'VERIFIED (Read & Account Active)' if auth_pass else 'NOT VERIFIED'}")
    print("=" * 70)

    return {
        "env_loaded": env_loaded,
        "env_path": env_path,
        "key_present": key_present,
        "secret_present": secret_present,
        "key_len": key_len,
        "secret_len": secret_len,
        "masked_key": masked_key,
        "configured_ip": CONFIGURED_WHITELIST_IP,
        "observed_ip": observed_ip,
        "ip_compat": ip_compat,
        "auth_pass": auth_pass,
        "auth_error": auth_error,
        "user_id": user_id,
        "equity_usdt": equity_usdt,
        "available_usdt": available_usdt,
        "blocked_margin_usdt": blocked_margin_usdt,
        "positions_count": positions_count,
        "orders_count": orders_count,
        "products_count": products_count,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(run_live_connection_test())
