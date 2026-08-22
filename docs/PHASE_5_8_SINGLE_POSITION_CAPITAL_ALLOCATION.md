# Phase 5.8: Single Active Position Lock, 100% Capital Allocation & Compounding Full-Market Rescan

## Executive Summary

Phase 5.8 establishes the core mathematical and execution invariants for QuantEdge AI:
1. **Single Active Trade Lock**: Exactly **ONE** active trade per trading account at any time. Any concurrent signal or attempt to execute a second trade fails closed with `SINGLE_TRADE_LIMIT_EXCEEDED`.
2. **100% Available Capital Dynamic Sizing**: Target position allocation sizes up to 100% of available margin balance (with a configurable fail-safe safety buffer, default 98.0%), quantized to Delta Exchange contract lot step sizes and strictly respecting leverage boundaries.
3. **Net Realized P&L Exchange Reconciliation**: Every closed position reconciles Gross P&L, trading fees, funding charges, and taxes to establish authoritative Net P&L:
   $$\text{Net P\&L} = \text{Gross P\&L} - \text{Trading Fees} - \text{Funding Costs} - \text{Taxes}$$
4. **Dynamic Capital Compounding**: The authoritative post-trade balance ($Balance_{new} = Balance_{pre} + Net\ P\&L$) serves as the exact base capital for the subsequent trade.
5. **Full-Market Rescan Orchestration**: While a trade is active, new-entry scanning across pairs is paused. Once Delta Exchange confirms the position is closed, the lock is released and a fresh full-market scan across all supported pairs (BTCUSD, ETHUSD, SOLUSD, XRPUSD) is automatically triggered.

---

## Architectural Components

```
+-----------------------------------------------------------------------------------+
|                           MarketScannerOrchestrator                              |
|   1. Verify Account Lock (is_locked)                                              |
|   2. If unlocked -> Scan supported pairs (BTCUSD, ETHUSD, SOLUSD, XRPUSD)        |
|   3. On TRADE_SETUP_READY inside entry zone -> Calculate 100% Capital Allocation  |
+-----------------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------------+
|                            SingleTradeLockManager                                 |
|   Atomic Thread-Safe Lock: Keyed by (user_id, account_id)                         |
|   - acquire_lock(): Rejects concurrent entries (SINGLE_TRADE_LIMIT_EXCEEDED)      |
|   - release_lock(): Called upon POSITION_CLOSED or validation rejection           |
|   - export_state() / load_state(): Survives engine restarts                       |
+-----------------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------------+
|                              CapitalAllocator                                     |
|   - Usable Margin = Available Balance * 98.0% Safety Buffer                       |
|   - Max Notional = Usable Margin * Leverage                                       |
|   - Raw Quantity = Max Notional / (Entry Price * Contract Unit)                   |
|   - Stepped Quantity = floor(Raw Quantity / Lot Size Step) * Lot Size Step       |
|   - Required Margin <= Available Balance (Fail-Closed Invariant)                  |
+-----------------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------------+
|                            TradeLifecycleManager                                  |
|   - Pre-trade balance captured at entry                                           |
|   - Bracket Protection (SL/TP) managed during trade lifecycle                     |
|   - close_position(): Reconciles Gross PnL, Fees, Funding -> Net PnL              |
|   - Post-trade balance updated -> Dynamic Compounding Base for next trade         |
|   - Releases SingleTradeLockManager lock -> Triggers fresh full-market rescan     |
+-----------------------------------------------------------------------------------+
```

---

## Mathematical Compounding Trace

| Cycle | Symbol | Pre Balance | Allocation | Gross PnL | Fees | Funding | Net PnL | Post Balance (Compounded) |
|---|---|---|---|---|---|---|---|---|
| **Trade 1** | BTCUSD | \$100.00 | 100% (\$98.00 usable) | +\$8.00 | \$0.00 | \$0.00 | **+\$8.00** | **\$108.00** |
| **Trade 2** | ETHUSD | \$108.00 | 100% (\$105.84 usable) | -\$5.00 | \$0.00 | \$0.00 | **-\$5.00** | **\$103.00** |
| **Trade 3** | SOLUSD | \$103.00 | 100% (\$100.94 usable) | +\$10.00 | \$1.50 | \$0.50 | **+\$8.00** | **\$111.00** |

---

## Verification & Test Results

- **Phase 5.8 Dedicated Tests**: 15 / 15 Passed (**100%**)
- **Phase 5.7 Configuration & Signal Tests**: 47 / 47 Passed (**100%**)
- **Full Engine Regression Suite**: **742 Passed, 1 Skipped, 0 Failed in 28.59s**
- **Frozen SMC Baseline (`b8095dc`)**: **ZERO DIFF** (Byte-for-byte identical on `structure.py`, `order_blocks.py`, `volatility.py`)
- **Frontend Production Build**: **Passed** (1602 modules transformed in 10.69s)
- **Live Delta Exchange Security**: **ZERO real orders placed during automated test suite execution**.
