# QuantEdge AI — Frontend UI/UX Design Specification
## Institutional-Grade Financial Trading Terminal & Intelligence Interface

---

## 1. Design Philosophy & Aesthetic Principles

QuantEdge AI is designed as a **high-precision, institutional-grade algorithmic trading terminal**. The user interface prioritizes high information density, low cognitive latency, instant market clarity, and zero-distraction dark mode ergonomics.

### Core Principles
1. **Financial Clarity & Density**: Key market numbers, P&L figures, and risk metrics are immediately scannable using tabular numbers (`font-mono` / tabular figures).
2. **Visual Hierarchy**: Primary market action (TradingView chart, active positions, order book) occupies the visual focal point; intelligence streams (news, macro calendar, AI radar) sit in contextual collapsible panels.
3. **Deterministic Color Semantics**: Colors are strictly reserved for financial meaning (Emerald for bullish/gains, Rose for bearish/losses, Cyan for brand/technical structure, Amber for risk alerts).
4. **Instant Status Transparency**: System connectivity, WebSocket health, and algo execution state are persistently visible in the global status bar.

---

## 2. Design Tokens & Visual Language

### 2.1 Color Palette
```css
/* Background & Surfaces */
--bg-terminal: #080B11;        /* Primary application canvas */
--bg-surface: #0E131F;         /* Container and card background */
--bg-surface-elevated: #151D2F;/* Hover states, dropdowns, active rows */
--bg-surface-modal: #1A243B;   /* Modals and dialog overlays */

/* Borders & Dividers */
--border-subtle: #1F293D;      /* Panel separators */
--border-focus: #06B6D4;       /* Focused inputs and active tabs */

/* Typography Colors */
--text-primary: #F8FAFC;       /* Primary headlines and market prices */
--text-secondary: #94A3B8;     /* Field labels and secondary metrics */
--text-muted: #64748B;         /* Timestamps and inactive elements */

/* Semantic Trading Accents */
--bullish: #10B981;            /* Buy, Long, Profit, Support OB */
--bullish-glow: rgba(16, 185, 129, 0.15);
--bearish: #F43F5E;            /* Sell, Short, Loss, Resistance OB */
--bearish-glow: rgba(244, 63, 94, 0.15);
--brand-cyan: #06B6D4;         /* QuantEdge AI accents, BOS lines */
--warning-amber: #F59E0B;      /* Macro warnings, volatility spikes */
```

### 2.2 Typography
- **Primary Font**: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `sans-serif` (Optimal UI legibility).
- **Monospace Financial Font**: `JetBrains Mono`, `Roboto Mono`, `monospace` (Used for all prices, sizes, order IDs, P&L figures, timestamps, and confidence percentages).

### 2.3 Spacing & Radius
- **Border Radius**: Sharp, modern financial feel: `rounded-md` (`6px`) for cards/buttons, `rounded-sm` (`3px`) for badges and tag chips.
- **Density**: Compact padding (`p-3` to `p-4` for cards, `py-1.5 px-3` for table cells).

---

## 3. Global Layout Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ GLOBAL HEADER: Logo | Market Ticker Bar (BTC, ETH, SOL) | System Health | Account | User   │
├─────────┬───────────────────────────────────────────────────────────────────┬───────────────┤
│         │ MAIN VIEWPORT CANVAS                                              │               │
│ SIDEBAR │                                                                   │ RIGHT DRAWER  │
│ (Nav)   │ • / (Executive Dashboard)                                         │ (Collapsible) │
│         │ • /terminal (TradingView + SMC Overlays + Order Ticket)           │               │
│ • Home  │ • /signals (SMC Setups + AI Signal Radar)                         │ • Breaking    │
│ • Trade │ • /intelligence (Live News + 15d Economic Calendar)               │   News Feed   │
│ • Radar │ • /orders (Live Order Book + Fills Ledger)                        │ • Macro Event │
│ • Intel │ • /positions (Positions + Real-Time P&L)                          │   Countdown   │
│ • Risk  │ • /risk-algo (Algo Controls + Emergency Kill-Switch)              │ • Notification│
│ • Keys  │ • /settings (API Keys & Security)                                 │   Center      │
│         │                                                                   │               │
├─────────┴───────────────────────────────────────────────────────────────────┴───────────────┤
│ GLOBAL FOOTER: Engine Status: ONLINE | 1H Stream | Delta WS: CONNECTED | Latency: 42ms      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component-by-Component Specifications

### 4.1 TradingView / Lightweight Charts Candlestick Engine
- **Component**: `TradingViewChart.tsx`
- **Data Source**: `GET /api/v1/market/candles?symbol=BTCUSD&interval=1h`
- **Features**:
  - Full candlestick series (Open, High, Low, Close, Volume).
  - Timeframe selector (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`). Default: `1h`.
  - **SMC Visual Layer**:
    - **Order Blocks**: Semi-transparent rectangular shaded zones (Green `#10B98120` for Bullish OB, Red `#F43F5E20` for Bearish OB) extending to current price until mitigated.
    - **BOS / CHOCH Break Lines**: Horizontal dotted reference lines marking swing high/low breaks.
    - **Fair Value Gaps (FVG)**: Shaded vertical imbalance bands.
    - **Entry / SL / TP Markers**: Horizontal dashed target lines with label pills.

### 4.2 AI Signal Radar & Confidence Meter
- **Component**: `AiSignalRadarCard.tsx`
- **Data Source**: `GET /api/v1/ai/enrichments` & `GET /api/v1/trade/signals`
- **Visuals**:
  - Circular radial gauge showing **Composite Confidence Score** (0–100%).
  - Multi-bar sub-scores:
    - Technical Alignment Score (`0–100`)
    - Market Regime Score (`0–100`)
    - Macro Risk Modifier (`-15 to +10`)
  - AI Recommendation Badge: `HIGH_CONFIDENCE_LONG`, `MODERATE_LONG`, `NEUTRAL`, `AVOID`.
  - Plain-English AI Reasoning summary card.

### 4.3 Live Categorized Financial News Stream
- **Component**: `FinancialNewsTicker.tsx`
- **Data Source**: `GET /api/v1/news?category=...&importance=...`
- **Features**:
  - Category pill filter tabs (`ALL`, `CRYPTO`, `MARKETS`, `CENTRAL_BANKS`, `REGULATION`, `ECONOMY`).
  - Sentiment badges (`BULLISH` in green, `BEARISH` in red, `NEUTRAL` in gray).
  - Importance indicator (`CRITICAL`, `HIGH`).
  - Strict 7-day retention tag (`Expires in X days`).
  - Direct canonical article URL link with external arrow icon.

### 4.4 15-Day Rolling Macroeconomic Calendar
- **Component**: `MacroCalendarWidget.tsx`
- **Data Source**: `GET /api/v1/economic-events`
- **Features**:
  - Grouped by release date with live countdown timer (`in 2h 15m`).
  - Country flag badge (`US`, `IN`, `EU`, `GB`, `JP`, `CN`).
  - Impact level visual pills: `HIGH` (Red), `MEDIUM` (Amber), `LOW` (Slate).
  - Comparison table: `Previous` vs `Forecast` vs `Actual` (instantly highlighted when actual is released).
  - Strict 24-hour post-event retention indicator.

### 4.5 Emergency Risk & Algo Control Panel
- **Component**: `RiskControlPanel.tsx`
- **Data Source**: `GET /api/v1/trade/status` & `POST /api/v1/trade/kill-switch`
- **Visuals**:
  - Large **EMERGENCY KILL-SWITCH** button with double-confirmation modal.
  - Algo Trading Toggle Switch (`ENABLED` / `DISABLED`) with instant visual feedback.
  - Account Margin Utilization bar (Green < 50%, Amber 50-80%, Red > 80%).

---

## 5. Responsive Behavior & Breakpoints

- **Desktop Trading Layout (`>= 1440px`)**: Full 3-column workstation (Navigation Sidebar + Chart & Order Terminal + Intelligence & News Drawer).
- **Laptop / Tablet Landscape (`1024px - 1439px`)**: Collapsible intelligence drawer, persistent 2-column trading interface.
- **Tablet Portrait (`768px - 1023px`)**: Tabbed interface switching between Chart, Orders, and Intelligence.
- **Mobile (`< 768px`)**: Single-column vertical scroll with sticky top ticker and bottom navigation bar.

---

## 6. Interaction & Motion Standards

- **Subtle Micro-Animations**: Smooth transitions on tab switching (150ms ease-out).
- **Price Flashes**: Real-time tick updates flash price cell briefly (Green for uptick, Red for downtick) for 300ms.
- **Loading Skeletons**: High-fidelity dark shimmer placeholders (`bg-slate-800/50 animate-pulse`) preventing layout shifts.
- **Error Boundaries**: Dedicated retry-enabled fallback cards for every independent widget.
