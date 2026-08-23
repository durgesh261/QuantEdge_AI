# QuantEdge AI — Phase 9 User App UI/UX Specification
## High-Precision Production User Trading Web Application

---

## 1. Product Overview & User Persona

The **QuantEdge User App** (`user-app/`) is an institutional-grade algorithmic trading web application built for quantitative traders, discretionary algorithmic traders, and portfolio managers.

### Core User Objectives
- Monitor algorithmic SMC strategy setups and AI confidence scores in real time.
- View live candlestick charts (Delta Exchange India `DELTAIN`) with visual SMC overlays (Order Blocks, FVGs, BOS/CHOCH break lines).
- Manage positions, orders, execution fills, and historical performance metrics.
- Maintain total risk control with dynamic capital allocation, account margin monitoring, and an emergency Kill-Switch.
- Consume real-time breaking financial news and macroeconomic calendar countdowns.

---

## 2. Complete Page-by-Page Inventory & UI/UX Blueprints

### Page 1: Secure Authentication (`/login` & `/signup`)
- **Purpose**: Authenticate users and establish secure HttpOnly JWT session cookies.
- **Components**: AuthCard, FormInput, PasswordField, SubmitButton, SecurityNotice.
- **Backend APIs**:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/signup`
- **States**:
  - *Loading*: Disabled button with spinner (`Authenticating...`).
  - *Error*: Inline badge (`Invalid credentials or account inactive`).
  - *Success*: Instant redirect to `/`.

---

### Page 2: Executive Trading Dashboard (`/`)
- **Purpose**: High-level command overview of account balance, algorithmic status, recent signals, and portfolio performance.
- **Layout**: 4-Metric Top Row + 2-Column Split (Live Radar & Active Positions / Market Feed).
- **Widgets**:
  1. **Portfolio Stat Cards**: Total Equity, 24h P&L ($ / %), Active Positions count, Margin Utilization %.
  2. **Algo Health Banner**: Connection status (`CONNECTED` / `DISCONNECTED`), Algo state (`ENABLED` / `DISABLED`), Active Trade Lock indicator.
  3. **Recent Signals Widget**: Top qualified SMC setups with AI confidence badges.
  4. **Active Positions Snapshot**: Mini position rows with quick close actions.
  5. **Breaking News Marquee**: Top 3 high-importance market headlines.
- **Backend APIs**:
  - `GET /api/v1/account/summary`
  - `GET /api/v1/trade/status`
  - `GET /api/v1/trade/positions`
  - `GET /api/v1/ai/enrichments?limit=5`
  - `GET /api/v1/news?importance=HIGH&limit=3`
- **States**:
  - *Loading*: Shimmer skeleton cards.
  - *Empty State (No connected exchange)*: Prominent banner: *"Connect your Delta Exchange India account to start trading"* with direct button to `/settings`.

---

### Page 3: Advanced Trading Terminal (`/terminal`)
- **Purpose**: The primary workstation combining TradingView charts, SMC overlays, order flow, active positions, and quick execution controls.
- **Layout**: 3-Pane Desktop Layout:
  - **Left / Center (65% width)**: Symbol selector + Timeframe bar + Lightweight Charts Canvas + Bottom Tabbed Tray (Positions / Open Orders / Fills / Strategy Setups).
  - **Right Sidebar (35% width)**: AI Signal Radar & Reasoning + Order Ticket + Market Depth & Ticker Summary.
- **Detailed Component Specifications**:
  1. **TradingView / Lightweight Candlestick Canvas**:
     - 1H canonical timeframe (with 1m, 5m, 15m, 4h, 1d view toggles).
     - Green/Red candlesticks with volume histogram below.
     - **SMC Visual Layer**:
       - *Bullish Order Blocks*: Semi-transparent green rectangle (`rgba(16, 185, 129, 0.18)`) with dotted upper/lower bounds and label: `Bullish OB (H1)`.
       - *Bearish Order Blocks*: Semi-transparent red rectangle (`rgba(244, 63, 94, 0.18)`) with dotted bounds and label: `Bearish OB (H1)`.
       - *BOS / CHOCH Break Lines*: Dotted horizontal line with cyan pill badge: `BOS ▲` or `CHOCH ▼`.
       - *Fair Value Gaps (FVG)*: Vertical hatched highlight band.
       - *Trade Markers*: Dashed horizontal lines for Entry (`Cyan`), Stop Loss (`Red`), Take Profit 1 & 2 (`Green`).
  2. **AI Signal Radar Card**:
     - Circular progress meter displaying **Composite Confidence Score** (e.g. `84%`).
     - Breakdown sub-meters: Technical Alignment (`90%`), Market Regime (`85%`), Macro Factor (`-5%`).
     - Plain-English AI analysis: *"Strong bullish structure alignment inside 1H unmitigated demand zone. Macro risk moderate due to upcoming CPI."*
  3. **Order Ticket (Manual / Algo Assisted)**:
     - Side selector: `BUY / LONG` (Green) vs `SELL / SHORT` (Red).
     - Order Type: `LIMIT`, `MARKET`, `STOP_LIMIT`.
     - Quantity input with balance percentage shortcuts (`25%`, `50%`, `75%`, `100%`).
     - Leverage slider (`1x` to `100x`, constrained by user risk settings).
     - Auto SL/TP calculation based on active Order Block bounds.
     - Dangerous Action Protection: Double-confirmation modal for manual market orders.
- **Backend APIs**:
  - `GET /api/v1/market/candles?symbol=BTCUSD&interval=1h`
  - `GET /api/v1/market/ticker/BTCUSD`
  - `GET /api/v1/trade/positions`
  - `GET /api/v1/trade/orders?status=OPEN`
  - `GET /api/v1/trade/signals?symbol=BTCUSD`
  - `GET /api/v1/ai/enrichments/BTCUSD`

---

### Page 4: Strategy Setups & AI Signal Radar (`/signals`)
- **Purpose**: Dedicated explorer for all algorithmically identified and qualified SMC trade setups across monitored markets.
- **Components**:
  - Filter bar: Symbol (`BTCUSD`, `ETHUSD`, `SOLUSD`), Direction (`LONG`, `SHORT`), Status (`QUALIFIED`, `ACTIVE`, `INVALIDATED`, `COMPLETED`).
  - Signal Grid Cards:
    - Setup ID badge (`setup_BTCUSD_H1_BULLISH_OB_...`).
    - Entry Price, Stop Loss, Take Profit 1 & 2, Risk-Reward Ratio (e.g. `RR: 1:2.85`).
    - AI Composite Confidence Badge (Color-coded: `>=80%` Emerald, `60-79%` Cyan, `<60%` Amber).
    - Status pill: `QUALIFIED` (waiting for entry), `ACTIVE` (position open), `COMPLETED` (TP hit), `STOPPED_OUT`.
- **Backend APIs**:
  - `GET /api/v1/trade/signals`
  - `GET /api/v1/ai/enrichments/{setupId}`

---

### Page 5: Live Market Intelligence (`/intelligence`)
- **Purpose**: Unified intelligence portal combining categorized financial news and the 15-day macroeconomic calendar.
- **Layout**: Two Split Tabs:
  1. **Financial & Crypto News Feed**:
     - Category filter pills: `ALL`, `CRYPTO`, `MARKETS`, `CENTRAL_BANKS`, `REGULATION`, `ECONOMY`, `COMMODITIES`.
     - Sentiment badges: `BULLISH` (Green), `BEARISH` (Rose), `NEUTRAL` (Slate).
     - Importance tags: `CRITICAL`, `HIGH`, `MEDIUM`.
     - Strict 7-day retention tag: `Expires in X days`.
     - Canonical source link with publisher attribution.
  2. **15-Day Macroeconomic Calendar**:
     - Grouped chronologically by date.
     - Live countdown badge (`in 3h 24m` or `Completed 1h ago`).
     - Country flag badge (`US`, `IN`, `EU`, `GB`, `JP`, `CN`, `CA`, `AU`).
     - Impact pill: `HIGH` (Red), `MEDIUM` (Amber), `LOW` (Gray).
     - Multi-column comparison: Event Name, Country, Previous, Forecast, Actual (dynamically updated).
     - Strict 24-hour post-event retention indicator.
- **Backend APIs**:
  - `GET /api/v1/news`
  - `GET /api/v1/economic-events`

---

### Page 6: Orders & Fills Ledger (`/orders`)
- **Purpose**: Comprehensive audit ledger for all open, filled, cancelled, and rejected orders.
- **Components**:
  - Tab 1: **Open Orders** (Cancel action button with confirmation).
  - Tab 2: **Order History** (Search by Symbol, Client Order ID, Status).
  - Tab 3: **Execution Fills** (Detailed execution price, filled quantity, transaction fee, timestamp).
- **Backend APIs**:
  - `GET /api/v1/trade/orders`
  - `GET /api/v1/trade/fills`

---

### Page 7: Positions & Realized P&L (`/positions`)
- **Purpose**: Active position monitor and closed trade performance ledger.
- **Components**:
  - **Open Positions Table**: Symbol, Side (`LONG`/`SHORT`), Size, Entry Price, Mark Price, Liquidation Price, Unrealized P&L ($ and %), Margin, Actions (Close Position, Modify SL/TP).
  - **Closed Trades History Table**: Entry Date, Exit Date, Symbol, Side, Entry/Exit Price, Realized Net P&L, Fees Paid, Exit Reason (`TAKE_PROFIT`, `STOP_LOSS`, `KILL_SWITCH`, `MANUAL`).
- **Backend APIs**:
  - `GET /api/v1/trade/positions`
  - `GET /api/v1/trade/history`

---

### Page 8: Risk Management & Algo Controls (`/risk-algo`)
- **Purpose**: Master control station for algorithmic trading rules, capital allocation, and emergency risk switches.
- **Components**:
  - **Emergency Kill-Switch Banner**: Large high-visibility red button: **ACTIVATE KILL SWITCH**. Immediately disables algo trading and cancels unplaced orders.
  - **Algo Master Switch**: Toggle button: `ALGO TRADING ACTIVE` (Green) / `ALGO PAUSED` (Amber).
  - **Risk Configuration Form**:
    - Max Risk % per trade (e.g. `1.0%` to `5.0%`).
    - Max Leverage (e.g. `5x` to `25x`).
    - Max Concurrent Open Trades (e.g. `1` to `3`).
    - Daily Max Drawdown Limit ($ and %).
- **Backend APIs**:
  - `GET /api/v1/account/algo-config`
  - `PUT /api/v1/account/algo-config`
  - `POST /api/v1/trade/algo/toggle`
  - `POST /api/v1/trade/kill-switch`
  - `POST /api/v1/trade/kill-switch/reset`

---

### Page 9: Account Settings & Exchange Keys (`/settings`)
- **Purpose**: Delta Exchange India API key configuration and security preferences.
- **Components**:
  - **Delta Exchange Connection Form**: API Key, API Secret (masked), Connect Button, Connection Status Badge (`CONNECTED`, `VERIFIED`, `DISCONNECTED`).
  - **Account Verification Tool**: Button to test connectivity and fetch live Delta balance without executing trades.
  - **Security Card**: Password update form, active session details.
- **Backend APIs**:
  - `POST /api/v1/account/connect`
  - `POST /api/v1/account/verify`
  - `POST /api/v1/account/disconnect`
  - `GET /api/v1/account/status`

---

### Page 10: In-App Notification Drawer (`/notifications` & Drawer Overlay)
- **Purpose**: Real-time alert center for critical market events, trade executions, and economic releases.
- **Features**:
  - Dropdown drawer from header + dedicated full-page view.
  - Filter: `All` vs `Unread`.
  - Severity colors: `CRITICAL` (Red), `HIGH` (Amber), `INFO` (Cyan).
  - Action: `Mark as Read`, `Mark All as Read`.
- **Backend APIs**:
  - `GET /api/v1/notifications`
  - `POST /api/v1/notifications/{id}/read`
  - `POST /api/v1/notifications/read-all`
