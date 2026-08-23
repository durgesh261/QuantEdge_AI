# QuantEdge AI — Phase 9 Implementation Roadmap
## Phased Rollout, Verification Gates & Safe Legacy Deprecation Strategy

---

## 1. Roadmap Principles & Safety Constraints

1. **Non-Destructive Execution**: The legacy `frontend/` directory is **never touched or deleted** until the new application is completely built, tested, verified, and explicitly approved by the user.
2. **Deterministic Quality Gates**: Every sub-phase contains automated and visual verification gates that must pass before advancing.
3. **Continuous SMC & Order Safety**: Backend tests (156 tests), Python engine tests (902 tests), and frozen SMC files (`ZERO DIFF`) are checked continuously during rollout.

---

## 2. Complete Phase Breakdown

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 PHASE 9 IMPLEMENTATION PIPELINE                             │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

  [ PHASE 9A: Deep Repository Audit ]  ──► (COMPLETE & FROZEN)
                 │
                 ▼
  [ PHASE 9B: User App Architecture & UI/UX Design ] ──► (SPEC COMPLETE)
                 │
                 ▼
  [ PHASE 9C: Developer App Architecture & Telemetry Spec ] ──► (SPEC COMPLETE)
                 │
                 ▼
  [ PHASE 9D: API / Data / Security Gap Analysis ] ──► (SPEC COMPLETE)
                 │
                 ▼
═══════════════════════════════════════════════════════════════════════════════════════════════
                    🔴 [ USER APPROVAL GATE — MUST OBTAIN EXPLICIT APPROVAL ]
═══════════════════════════════════════════════════════════════════════════════════════════════
                 │
                 ▼
  [ PHASE 9E: Scaffold & Implement User App (`user-app/`) ]
  ├── Step 1: Vite + React 18 + TypeScript + Tailwind CSS Design System
  ├── Step 2: Auth Store (JWT Cookies), Layout Shell, Header Tickers
  ├── Step 3: TradingView / Lightweight Charts Engine + SMC Overlays (OB, FVG, BOS/CHOCH)
  ├── Step 4: Executive Dashboard & Signal Radar Cards
  ├── Step 5: Live Market Intelligence (Categorized News + 15d Economic Calendar)
  ├── Step 6: Orders, Positions, Execution Fills, and Realized P&L Ledger
  └── Step 7: Risk Management Panel, Algo Toggle, and Emergency Kill-Switch
                 │
                 ▼
  [ PHASE 9F: Scaffold & Implement Developer App (`developer-app/`) ]
  ├── Step 1: Vite + React 18 + TypeScript + Standalone Dev Layout Shell
  ├── Step 2: RBAC Gate (`ROLE_DEVELOPER`, `ROLE_ADMIN`) + 403 Redirection
  ├── Step 3: Developer Command Center & Platform Vitals Telemetry
  ├── Step 4: Multi-Tenant Account Health & Active Trade Lock Inspector
  ├── Step 5: External Provider Sync Telemetry (Delta, CryptoCompare, Faireconomy)
  ├── Step 6: Sanitized Real-Time Log Viewer Terminal & Latency Prober
  └── Step 7: Strategy Sandbox Lab & Simulated Price Tick Runner
                 │
                 ▼
  [ PHASE 9G: End-to-End Integration Testing & Feature Parity Comparison ]
  ├── Automated Cypress / Playwright E2E suites against live Spring Boot backend
  ├── Feature-by-feature comparison against legacy `frontend/`
  └── Mobile & Tablet responsive breakpoint validation
                 │
                 ▼
  [ PHASE 9H: Production Build & Security Audit ]
  ├── Run `npm run build` in both `user-app/` and `developer-app/`
  ├── Verify zero credential leakage and zero bundle cross-contamination
  ├── Run Backend Maven tests (156 tests) & Python pytest suite (902 tests)
  └── Verify frozen SMC core (`ZERO DIFF`)
                 │
                 ▼
═══════════════════════════════════════════════════════════════════════════════════════════════
                    🔴 [ FINAL USER APPROVAL GATE FOR LEGACY CLEANUP ]
═══════════════════════════════════════════════════════════════════════════════════════════════
                 │
                 ▼
  [ PHASE 9I: Deprecate Legacy Frontend ]
  └── Update root `docker-compose.yml` to serve `user-app` (Port 3000) and `developer-app` (Port 3001)
                 │
                 ▼
  [ PHASE 9J: Clean Removal of Legacy `frontend/` Directory ]
  ├── Remove legacy `frontend/` directory after explicit confirmation
  ├── Verify clean Git working tree
  └── Commit: `feat(phase9): launch high-performance user trading app and dedicated developer observability app`
```

---

## 3. Risk Matrix & Mitigation Strategies

| Risk Description | Severity | Mitigation Strategy |
| :--- | :--- | :--- |
| **Candlestick Chart Performance Latency** | High | Use Canvas-rendered TradingView Lightweight Charts; memoize candle series; throttle updates to 250ms. |
| **Cross-Tenant Data Exposure** | Critical | Server-side query filtering by `user_id` on every query; automated IDOR security tests in CI. |
| **Accidental Order Placement from Client** | Critical | Client has zero exchange credentials; all executions route strictly through `OrderExecutionService.java:312`. |
| **SMC Calculation Drift** | Critical | Core SMC Python engine remains strictly frozen with continuous automated diff checking. |
| **Breaking Legacy During Migration** | Medium | `frontend/` remains untouched in parallel until `user-app/` is 100% verified. |
