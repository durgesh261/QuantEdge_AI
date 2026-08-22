# QuantEdge AI — Phase 5.9: Authoritative Order Block Stop Loss, Dynamic Leverage (35% Max Loss), ROE-based Take Profit (60% Target) & Compounding

## 1. Executive Summary

Phase 5.9 establishes the core mathematical and risk integrity framework for real algo trading execution on Delta Exchange India. Stop Loss is derived authoritatively from the structural boundaries of the detected Smart Money Concept (SMC) Order Block, Leverage is dynamically computed to guarantee that the planned loss at SL never exceeds 35% of allocated trade capital, and Take Profit is calculated dynamically from target Return on Equity (ROE, defaulting to 60.0% return on allocated trading margin).

---

## 2. Mandatory Trading Rules & Mathematics

### 2.1 Authoritative Stop Loss derived from Order Block Zones
- **Demand / Bullish Order Block (LONG)**:
  $$\text{Stop Loss} = \text{Order Block Bottom Price} = \text{ob.bottom\_price}$$
  $$\text{Entry Price} = \begin{cases} \text{ob.top\_price} - (\text{ob.width} \times 0.25) & \text{if wide OB} (\text{width} > 0.6\%) \\ \text{ob.top\_price} & \text{if standard OB} \end{cases}$$
- **Supply / Bearish Order Block (SHORT)**:
  $$\text{Stop Loss} = \text{Order Block Top Price} = \text{ob.top\_price}$$
  $$\text{Entry Price} = \begin{cases} \text{ob.bottom\_price} + (\text{ob.width} \times 0.25) & \text{if wide OB} (\text{width} > 0.6\%) \\ \text{ob.bottom\_price} & \text{if standard OB} \end{cases}$$
- **Zero-Arbitrary Rule**: SL is strictly determined by the market structure zone. The frontend cannot override or tamper with the stop loss price.

### 2.2 Dynamic Leverage Calculation (35% Max Loss Guarantee)
To ensure the maximum loss when the SL price is hit is exactly or strictly within 35.0% of the capital allocated:
$$\text{stopDistanceFraction} = \frac{|\text{entryPrice} - \text{stopLossPrice}|}{\text{entryPrice}}$$
$$\text{stopDistancePct} = \text{stopDistanceFraction} \times 100$$
$$\text{calculatedLeverage} = \max\left(1, \left\lfloor \frac{35.0\%}{\text{stopDistancePct}} \right\rfloor\right) = \max\left(1, \left\lfloor \frac{0.35}{\text{stopDistanceFraction}} \right\rfloor\right)$$

#### Verification Examples:
- **1.0% SL Distance**: $\lfloor 35.0 / 1.0 \rfloor = 35\text{x}$ leverage $\to$ Planned loss = $35 \times 1.0\% = 35.0\% \le 35.0\%$.
- **2.0% SL Distance**: $\lfloor 35.0 / 2.0 \rfloor = 17\text{x}$ leverage $\to$ Planned loss = $17 \times 2.0\% = 34.0\% \le 35.0\%$.
- **5.0% SL Distance**: $\lfloor 35.0 / 5.0 \rfloor = 7\text{x}$ leverage $\to$ Planned loss = $7 \times 5.0\% = 35.0\% \le 35.0\%$.
- **10.0% SL Distance**: $\lfloor 35.0 / 10.0 \rfloor = 3\text{x}$ leverage $\to$ Planned loss = $3 \times 10.0\% = 30.0\% \le 35.0\%$.
- **Unsupported Leverage**: If required leverage exceeds Delta instrument maximum (e.g. 175x on a 100x max instrument), the trade setup is rejected fail-closed (`CapitalAllocationError`).

### 2.3 ROE-Based Take Profit Calculation (60.0% Target Default)
Target ROE defines the percentage return on the allocated margin equity:
$$\text{Price Movement Fraction} = \frac{\text{takeProfitTargetPct}}{100 \times \text{calculatedLeverage}}$$
- **LONG Take Profit**:
  $$\text{TP Price} = \text{entryPrice} \times (1 + \text{Price Movement Fraction})$$
- **SHORT Take Profit**:
  $$\text{TP Price} = \text{entryPrice} \times (1 - \text{Price Movement Fraction})$$
- **Return on Margin Verification**:
  $$\text{Realized ROE} = \text{calculatedLeverage} \times \frac{|\text{TP} - \text{Entry}|}{\text{Entry}} = \text{takeProfitTargetPct} = 60.0\%$$

### 2.4 User Configuration Overrides & Version Snapshot Immutability
- Users can update target TP (e.g. from 60.0% to 80.0%), generating Version 2 of `AlgoConfiguration`.
- Version 2 applies to all subsequent trade setups.
- Existing open and historical trades retain their immutable Version 1 snapshot (`AlgoConfigurationSnapshot`).

### 2.5 Single Active Trade Lock & Compounding
- Only ONE trade may be active per trading account across all supported pairs.
- Market scanner halts entry evaluation while a position or pending entry exists.
- Position closure triggers authoritative Delta balance reconciliation:
  $$\text{Net P&L} = \text{Gross P&L} - \text{Trading Fees} - \text{Funding Costs}$$
  $$\text{New Compounded Balance} = \text{Previous Balance} + \text{Net P&L}$$
- Single-trade lock is released only after reconciliation, enabling 100% of the new compounded balance for the next qualified setup.

---

## 3. Implementation Verification & Regression Results

| Test Category | Test Count | Result |
| :--- | :--- | :--- |
| Phase 5.9 Authoritative SL, Dynamic Leverage, ROE TP & Compounding Suite | 21 | PASS (100%) |
| Phase 5.8 Single Trade Lock & Capital Allocator Suite | 15 | PASS (100%) |
| Phase 5.7 Algo Configuration & Versioning Suite | 22 | PASS (100%) |
| Phase 5.6 Private WebSocket & Real-Time Sync Suite | 23 | PASS (100%) |
| Phase 5.5 Delta Account Connection Suite | 11 | PASS (100%) |
| Phase 5.4 Real Order Execution Suite | 40 | PASS (100%) |
| Phase 4 Strategy & Risk/Reward Validation Suites | 86 | PASS (100%) |
| Phase 3 SMC Core Ingestion, Invariance & Detection Suites | 545 | PASS (100%) |
| **Total Engine Pytest Suite** | **764 (763 pass, 1 skip, 0 fail)** | **PASS** |
| **Frontend Production Build (`npm run build`)** | **1602 modules** | **PASS** |
| **Frozen SMC Core (`structure.py`, `order_blocks.py`, `volatility.py`)** | **Baseline `b8095dc`** | **ZERO DIFF** |
| **Real Orders Placed During Testing** | **None** | **ZERO (0)** |
