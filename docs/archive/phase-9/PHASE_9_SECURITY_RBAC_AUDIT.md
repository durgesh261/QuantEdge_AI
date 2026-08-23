# QuantEdge AI — Phase 9 Security & RBAC Audit
## Multi-Tenant Security Architecture, Role-Based Access Control & Credential Protection

---

## 1. Authentication & Session Architecture

QuantEdge AI implements a stateless, token-based authentication model designed to eliminate CSRF vulnerabilities and prevent client-side credential theft:

```
┌─────────────────┐           POST /api/v1/auth/login            ┌─────────────────────┐
│  React Client   ├─────────────────────────────────────────────►│  Spring Boot Auth   │
│  (User / Dev)   │◄─────────────────────────────────────────────┤  (BCrypt + JWT)     │
└────────┬────────┘   Set-Cookie: access_token=...; HttpOnly     └──────────┬──────────┘
         │            Set-Cookie: refresh_token=...; HttpOnly               │
         │                                                                  ▼
         │            GET /api/v1/trade/orders (Cookie attached) ┌─────────────────────┐
         └──────────────────────────────────────────────────────►│  JwtAuthentication  │
                                                                 │  Filter validates   │
                                                                 │  User & Tenancy     │
                                                                 └─────────────────────┘
```

- **JWT Storage**: Stored exclusively in `HttpOnly`, `SameSite=Lax` cookies. JavaScript running in the browser cannot read or exfiltrate session tokens.
- **Token Lifespan**:
  - `access_token`: 24 Hours.
  - `refresh_token`: 7 Days.
- **Password Security**: Passwords are never stored in plain text; hashed using `BCryptPasswordEncoder` with strength 12.

---

## 2. Role-Based Access Control (RBAC) Matrix

Users possess an authoritative `role` attribute in the `users` table (`V3__add_user_role.sql`):
1. **`ROLE_USER`**: Standard algorithmic and discretionary trader.
2. **`ROLE_DEVELOPER`**: Quantitative engineer and technical operator.
3. **`ROLE_ADMIN`**: Full platform super-administrator.

```
┌───────────────────────────────────────┬───────────┬────────────────┬────────────┐
│ API Endpoint Group                    │ ROLE_USER │ ROLE_DEVELOPER │ ROLE_ADMIN │
├───────────────────────────────────────┼───────────┼────────────────┼────────────┤
│ /api/v1/auth/** (Profile & Session)   │ ✅ Own    │ ✅ Own         │ ✅ Own     │
│ /api/v1/account/** (Keys & Balance)   │ ✅ Own    │ ✅ Own         │ ✅ Own     │
│ /api/v1/trade/** (Orders & Positions) │ ✅ Own    │ ✅ Own         │ ✅ Own     │
│ /api/v1/market/** (Public Market Data)│ ✅ Public │ ✅ Public      │ ✅ Public  │
│ /api/v1/news/** (News Ingestion Read) │ ✅ Public │ ✅ Public      │ ✅ Public  │
│ /api/v1/news/refresh (Manual Sync)    │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/economic-events/** (Calendar) │ ✅ Public │ ✅ Public      │ ✅ Public  │
│ /api/v1/economic-events/sync (Sync)   │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/notifications/** (User Alerts)│ ✅ Own    │ ✅ Own         │ ✅ Own     │
│ /api/v1/ai/** (Signal Intelligence)   │ ✅ Own    │ ✅ Own         │ ✅ Own     │
│ /api/v1/developer/status              │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/developer/system/accounts     │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/developer/diagnostics         │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/developer/logs (Sanitized)    │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/v1/developer/sandbox/**          │ ❌ 403    │ ✅ Allowed     │ ✅ Allowed │
│ /api/engine/** (Internal Python Pipe) │ ❌ 401/403│ ❌ 401/403     │ ❌ 401/403 │
└───────────────────────────────────────┴───────────┴────────────────┴────────────┘
```

---

## 3. Multi-Tenant Isolation & IDOR Protections

### 3.1 Server-Side Tenancy Filter
Every database query in `TradingQueryService.java` and `AccountManagementService.java` explicitly includes the authenticated user's ID:
```java
// Immutable Tenancy Verification Pattern
TradingAccount account = accountRepository.findById(accountId)
        .orElseThrow(() -> new ResourceNotFoundException("Account not found"));

if (!account.getUser().getId().equals(authenticatedUser.getId())) {
    log.warn("IDOR attempt detected: User {} tried to access Account {} owned by User {}",
            authenticatedUser.getId(), account.getId(), account.getUser().getId());
    throw new AccessDeniedException("Access denied to requested account");
}
```
- **Automated Invariant**: Verified by unit test `TradingQueryServiceTest$TenantIsolationTests`.

---

## 4. Exchange Credential Protection (Delta India API Keys)

- **Storage**: Delta Exchange India API keys and secrets are encrypted with **AES-256-GCM** before persistence in `delta_connections`.
- **Zero Key Leaks**:
  - `AccountController.java` never returns decrypted API secrets.
  - `TradingQueryService.java` returns sanitized DTOs without credentials.
  - The Python engine receives zero credentials; order execution is performed exclusively inside Spring Boot (`OrderExecutionService.java:312`).
  - Frontend state never holds or persists raw exchange secrets.

---

## 5. Developer Telemetry Data Redaction

In `DeveloperService.java`, all log entries and diagnostic payloads are scrubbed using regular expression filters before transmission to the Developer App:
```java
// Automatic Redaction Pattern
logContent.replaceAll("(?i)(api[_-]?secret|password|secret|token|key)[:=]\\s*['\"]?[a-zA-Z0-9_-]{8,}['\"]?", "$1=***REDACTED***")
```
- Passwords, JWT secrets, Delta API secrets, and encryption keys are scrubbed with `***REDACTED***`.
