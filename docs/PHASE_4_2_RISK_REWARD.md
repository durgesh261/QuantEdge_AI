# Phase 4.2 Risk/Reward & Final Trade Setup Validation

## RiskRewardConfig

Phase 4.2 implements a deterministic, fully configurable risk/reward parameters container:

```python
@dataclass(frozen=True)
class RiskRewardConfig:
    minimum_risk_reward: Decimal = Decimal("2.0")
    reward_multiple: Decimal = Decimal("2.0")
```

### Validation Constraints:
- `minimum_risk_reward > 0` (raises `ValueError` otherwise).
- `reward_multiple > 0` (raises `ValueError` otherwise).
- Supports runtime customization (e.g., 1.5, 2.0, 2.5, 3.0) without modifying production source code.

---

## Entry

- **Authoritative Source**: `ob.calculate_entry_price()`.
- Derived dynamically based on OB width percentage:
  - Narrow OBs ($\le 0.6\%$): Edge entry (`top_price` for Bullish, `bottom_price` for Bearish).
  - Wide OBs ($> 0.6\%$): 25% boundary offset (`top_price - 0.25 * width` for Bullish, `bottom_price + 0.25 * width` for Bearish).
- If entry cannot be calculated, the setup does not become `TRADE_SETUP_READY` and returns `"entry or stop loss could not be calculated"`.

---

## Stop Loss

- **Authoritative Source**: `ob.calculate_stop_loss()`.
- Derived strictly from the opposite OB boundary:
  - Bullish OB: `bottom_price`.
  - Bearish OB: `top_price`.
- If stop loss cannot be calculated, the setup does not become `TRADE_SETUP_READY` and returns `"entry or stop loss could not be calculated"`.

---

## Take Profit

Calculated deterministically without future price knowledge:
- **LONG**: $\text{take\_profit} = \text{entry} + (\text{risk\_distance} \times \text{reward\_multiple})$
- **SHORT**: $\text{take\_profit} = \text{entry} - (\text{risk\_distance} \times \text{reward\_multiple})$

No proprietary or unverified LuxAlgo TP formulas are used. Calculations use exact `Decimal` arithmetic.

---

## Risk

- Defined as:
  - **LONG**: $\text{risk\_distance} = \text{entry} - \text{stop\_loss}$
  - **SHORT**: $\text{risk\_distance} = \text{stop\_loss} - \text{entry}$
- Must be strictly positive ($> 0$).
- **Price Geometry Validation**:
  - LONG requires $\text{entry} > \text{stop\_loss}$. If invalid: `"invalid risk geometry: entry must be > stop_loss for LONG"`.
  - SHORT requires $\text{stop\_loss} > \text{entry}$. If invalid: `"invalid risk geometry: stop_loss must be > entry for SHORT"`.

---

## Reward

- Defined as:
  - **LONG**: $\text{reward\_distance} = \text{take\_profit} - \text{entry}$
  - **SHORT**: $\text{reward\_distance} = \text{entry} - \text{take\_profit}$
- Must be strictly positive ($> 0$).

---

## Risk/Reward

- Ratio: $\text{risk\_reward} = \frac{\text{reward\_distance}}{\text{risk\_distance}}$
- Example with default `reward_multiple = 2.0`:
  - Entry = 100, Stop Loss = 95, Risk = 5, Take Profit = 110, Reward = 10 $\implies$ $\text{RR} = 2.0$.

---

## Minimum RR Filter

- Only setups with $\text{risk\_reward} \ge \text{minimum\_risk\_reward}$ transition to `SetupState.TRADE_SETUP_READY`.
- If $\text{risk\_reward} < \text{minimum\_risk\_reward}$:
  - Retains `SetupState.QUALIFIED_LONG` or `SetupState.QUALIFIED_SHORT`.
  - `trade_setup_ready` is `False`.
  - Reason explicitly noted: `"risk_reward below minimum threshold"`.

---

## LONG

A complete qualified Bullish setup transitions to `TRADE_SETUP_READY` when:
1. `SetupState` is `QUALIFIED_LONG` (Active bullish OB + price inside OB + bullish structure confirmation).
2. Valid entry and stop loss exist.
3. $\text{entry} > \text{stop\_loss}$ (positive risk).
4. $\text{take\_profit} > \text{entry}$ (positive reward).
5. $\text{risk\_reward} \ge \text{minimum\_risk\_reward}$.

```json
{
  "setup_state": "TRADE_SETUP_READY",
  "direction": "LONG",
  "trade_setup_ready": true,
  "entry": "49750.000",
  "stop_loss": "49000.000",
  "take_profit": "51250.000",
  "risk_distance": "750.000",
  "reward_distance": "1500.000",
  "risk_reward": "2.0"
}
```

---

## SHORT

A complete qualified Bearish setup transitions to `TRADE_SETUP_READY` when:
1. `SetupState` is `QUALIFIED_SHORT` (Active bearish OB + price inside OB + bearish structure confirmation).
2. Valid entry and stop loss exist.
3. $\text{stop\_loss} > \text{entry}$ (positive risk).
4. $\text{entry} > \text{take\_profit}$ (positive reward).
5. $\text{risk\_reward} \ge \text{minimum\_risk\_reward}$.

```json
{
  "setup_state": "TRADE_SETUP_READY",
  "direction": "SHORT",
  "trade_setup_ready": true,
  "entry": "60250.000",
  "stop_loss": "61000.000",
  "take_profit": "58750.000",
  "risk_distance": "750.000",
  "reward_distance": "1500.000",
  "risk_reward": "2.0"
}
```

---

## Long-Lived OB

- Order Block validity does not degrade over time.
- A 6-month-old untouched OB (4,320+ candles) calculates entry, stop loss, take profit, risk/reward, and successfully becomes `TRADE_SETUP_READY` upon retest.

---

## Multiple OBs

- When multiple active OBs overlap at the current price, candidate selection remains strictly deterministic using the Phase 4.1 priority ranking:
  1. Direction matching confirmed structure trend.
  2. Higher confidence score.
  3. Narrower zone width.
  4. Formation index tiebreaker.

---

## Duplicate Protection

- Duplicate closed candle evaluations produce identical `StrategyDecision`, `setup_id`, and `take_profit` with 0 duplicate objects or state alterations.

---

## Future-Data Invariance

- The TP and RR calculations for candle $T$ depend exclusively on state $\le T$ and configuration. Future candles $T+1 \dots T+N$ do not alter the historical setup at $T$.

---

## Incremental vs Replay

- Processing candles incrementally vs full batch replay produces 100% identical `setup_state`, `direction`, `entry`, `stop_loss`, `take_profit`, `risk_reward`, and `trade_setup_ready`.

---

## Read-Only SMC

- Risk and reward validation is purely observational.
- Does NOT mutate OB state, touch count, invalidation flags, pivots, breaks, or canonical CSV files.

---

## UTC / Asia/Kolkata

- **Internal Canonical**: `StrategyDecision.timestamp` is timezone-aware UTC (`UTC`).
- **User-Facing Presentation**: `StrategyDecision.timestamp_ist` formats dynamically in `Asia/Kolkata` (`UTC+05:30`).

---

## Tests

New dedicated test suite: [`engine/tests/test_phase4_2_risk_reward.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_2_risk_reward.py) (34/34 passed).

All 34 Scenarios Verified:
1. LONG valid RR $\to$ `TRADE_SETUP_READY`
2. SHORT valid RR $\to$ `TRADE_SETUP_READY`
3. RR exactly at minimum $\to$ `TRADE_SETUP_READY`
4. RR below minimum $\to$ `QUALIFIED_LONG/SHORT` (not ready)
5. RR above minimum $\to$ `TRADE_SETUP_READY`
6. Zero risk distance rejected
7. Negative risk geometry LONG (entry $\le$ SL) rejected
8. Negative risk geometry SHORT (SL $\le$ entry) rejected
9. Invalid entry returns factual reason
10. Invalid stop loss returns factual reason
11. TP LONG calculation verified
12. TP SHORT calculation verified
13. Decimal precision arithmetic verified
14. Configurable RR threshold verified
15. Configurable reward multiple verified
16. Invalid configuration raises `ValueError`
17. 6-month-old untouched bullish OB $\to$ `TRADE_SETUP_READY`
18. 6-month-old untouched bearish OB $\to$ `TRADE_SETUP_READY`
19. Old OB not displaced by newer OB
20. Multiple OB deterministic selection verified
21. Forming candle rejected
22. Closed candle accepted
23. Duplicate evaluation idempotency verified
24. Future-data invariance verified
25. Incremental $\equiv$ Replay equivalence verified
26. Zero SMC mutation guarantee
27. Zero CSV modification guarantee
28. Deterministic `setup_id` verified
29. Deterministic reasons list verified
30. UTC timestamp retention verified
31. IST display presentation verified
32. Zero order execution / placement methods verified
33. Zero Binance dependencies verified
34. Frozen SMC files verified unchanged

---

## Phase 3/4 Regression

Full test suite execution:
```text
======================= 536 passed, 1 skipped in 20.81s =======================
```
- **Total Tests**: 537
- **Passed**: 536 (100% pass rate)
- **Skipped**: 1 (pre-existing TV sync skip)
- **Failed**: 0

---

## Frozen SMC

```bash
$ git diff b8095dc -- engine/src/quantedge/smc/structure.py \
                     engine/src/quantedge/smc/order_blocks.py \
                     engine/src/quantedge/smc/volatility.py
# Output: EMPTY (ZERO DIFF)
```
- [`engine/src/quantedge/smc/structure.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/structure.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/order_blocks.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/order_blocks.py) — **ZERO DIFF**
- [`engine/src/quantedge/smc/volatility.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/smc/volatility.py) — **ZERO DIFF**

---

## Repository Cleanliness

- Working tree clean.
- Strictly no live order execution, private exchange API connections, or wallet management implemented.

---

## Files Changed
1. [`engine/src/quantedge/strategy/models.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/models.py): Added `TRADE_SETUP_READY` to `SetupState`, created `RiskRewardConfig`, added `risk_distance`, `reward_distance`, `take_profit`, `risk_reward`, and `is_trade_setup_ready` on `StrategyDecision`.
2. [`engine/src/quantedge/strategy/engine.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/engine.py): Implemented entry, stop loss, take profit, risk/reward calculation, and minimum RR filtering.
3. [`engine/src/quantedge/strategy/__init__.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/src/quantedge/strategy/__init__.py): Exported `RiskRewardConfig`.
4. [`engine/tests/test_phase4_1_signal_qualification.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_1_signal_qualification.py): Updated assertions for backward compatibility with `TRADE_SETUP_READY`.
5. [`engine/tests/test_phase4_2_risk_reward.py`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/engine/tests/test_phase4_2_risk_reward.py): 34 unit and regression tests for Phase 4.2.
6. [`docs/PHASE_4_2_RISK_REWARD.md`](file:///c:/Users/durge/OneDrive/Desktop/Antigravity%20App/QuantEdge%20AI/docs/PHASE_4_2_RISK_REWARD.md): Phase 4.2 validation report.

---

## Final Verdict

# `RISK_REWARD_VALIDATED`
