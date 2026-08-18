# Risk Specification - QuantEdge AI V2

## Overview

Risk management is enforced at **two layers**:
1. **Python Engine** - Calculates position sizing, SL/TP per strategy
2. **Spring Boot** - **Authoritative** validation & enforcement (cannot be bypassed)

## Risk Parameters

| Parameter | Value | Source |
|-----------|-------|--------|
| Risk per Trade | 35% of account balance | Strategy Config |
| Target Reward | 60% of account balance | Strategy Config |
| Max Leverage | 100x | Strategy Config |
| Max Concurrent Trades | 1 | Strategy Config |
| Max Daily Loss | Configurable | Risk Config |
| Max Drawdown | Configurable | Risk Config |

## Capital Model

- **100% of available account balance** is allocation basis
- System compounds from current net account balance
- No hardcoded starting capital after account creation
- Balance = free margin + used margin + unrealized P&L

## Position Sizing

```
riskAmount = accountBalance * 0.35
riskDistance = |entryPrice - stopLossPrice|
positionSize = riskAmount / riskDistance
```

### Leverage Calculation

```
notionalValue = positionSize * entryPrice
requiredLeverage = notionalValue / accountBalance
actualLeverage = min(requiredLeverage, maxLeverage)
```

If leverage capped:
- Recalculate positionSize with max leverage
- Verify actual risk <= target risk

## Stop Loss Rules

| Direction | Stop Loss Price |
|-----------|-----------------|
| LONG | Lower OB boundary (bottom_price) |
| SHORT | Upper OB boundary (top_price) |

**Legacy Rule Removed**: No ATR-based offset (OB boundary +/- 0.25 ATR)

## Take Profit Rules

```
rewardAmount = accountBalance * 0.60
priceMove = rewardAmount / positionSize

LONG:  TP = entryPrice + priceMove
SHORT: TP = entryPrice - priceMove
```

Target account-level R:R = 60/35 = 1.714

**Legacy Rules Removed**:
- TP1 = 1.5R, TP2 = 2.8R
- Minimum RR = 2.5
- Partial take profits

## Account-Level Risk Checks (Spring Boot)

Before ANY order execution, Spring Boot verifies:

1. **Authentication** - Valid user session
2. **Account Ownership** - User owns the trading account
3. **Account Active** - Account not disabled
4. **ALGO Enabled** - User's algoEnabled = true
5. **Delta Enabled** - User's deltaEnabled = true (for live)
6. **Trading Mode** - PAPER/LIVE matches account type
7. **One Active Trade** - No other open strategy trade
8. **Balance Check** - Sufficient free margin
9. **Risk Limits** - Position within risk parameters
10. **Leverage Cap** - Leverage <= 100x
11. **Symbol Allowed** - Symbol in strategy config
12. **Price Sanity** - Entry/SL/TP within reasonable bounds
13. **Idempotency** - Duplicate order protection

## Multi-User Isolation

Each user's risk is completely independent:

- User A's positions don't affect User B's margin
- User A's daily loss limit doesn't affect User B
- User A's drawdown doesn't affect User B
- No shared risk pool

## Delta Credentials Security

- Stored encrypted in database (AES-256-GCM)
- Never returned to frontend (only connection status)
- Spring Boot decrypts only for order submission
- Rotation supported via re-encryption

## Paper Trading Risk

Paper trading uses **identical risk logic** as live:
- Simulated balance/equity
- Same position sizing
- Same SL/TP calculation
- Simulated fills with slippage/commission
- Separate paper trading accounts per user

## Audit Trail

Every risk decision logged:
- Order validation result
- Risk parameter values used
- Calculated position size & leverage
- Rejection reasons (if any)
- User/account/context

## Configuration Per Trading Account

```yaml
risk_configuration:
  risk_per_trade_pct: 35.0      # 35% of balance
  target_reward_pct: 60.0       # 60% of balance
  max_leverage: 100             # Hard cap
  max_concurrent_trades: 1      # Strategy limit
  max_daily_loss_pct: 50.0      # Optional: halt at 50% daily loss
  max_drawdown_pct: 80.0        # Optional: halt at 80% drawdown
```

## Emergency Controls

- **Kill Switch** - Flatten all positions (admin only)
- **Account Disable** - Stop all trading for account
- **Global Halt** - Emergency stop (admin only)
- **Max Leverage Override** - Reduce leverage globally

## Testing Requirements

| Test Case | Expected |
|-----------|----------|
| 35% risk calculation | Exact decimal match |
| 60% reward calculation | Exact decimal match |
| Position sizing | riskAmount / |entry-SL| |
| Leverage cap at 100x | Never exceeds 100 |
| Long SL = OB bottom | Exact price match |
| Short SL = OB top | Exact price match |
| TP calculation | Account growth 60% |
| Zero distance rejection | Error |
| Insufficient balance | Error |
| Invalid price (negative) | Error |
| User A cannot access User B | 403 Forbidden |