# Security Specification - QuantEdge AI V2

## Threat Model

### Assets to Protect
- User credentials (password hashes)
- Delta API keys/secrets (encrypted)
- Trading account balances & positions
- Order history & P&L
- Personal information

### Attack Vectors
- Credential stuffing
- Session hijacking
- API key theft
- Order manipulation
- Privilege escalation
- Data exfiltration

## Authentication Security

### Password Handling
- **Algorithm**: BCrypt with cost factor 12
- **Minimum length**: 8 characters
- **No password reuse**: Not implemented (future)
- **Breach checking**: Not implemented (future)

### JWT Tokens
- **Algorithm**: HS256 (256-bit secret minimum)
- **Access token**: 24 hours, HttpOnly cookie
- **Refresh token**: 7 days, HttpOnly cookie
- **Rotation**: Refresh rotates access token
- **Revocation**: Token blacklist on logout

### Session Management
- Stateless JWT (no server sessions)
- Short access token lifetime
- Refresh token rotation
- Automatic logout on token expiry

## Authorization

### Role-Based Access
- Single role: `ROLE_USER`
- All users equal permissions
- Resource ownership enforced in service layer

### Resource Ownership
```java
// Every service method verifies ownership
tradingAccountRepository.findByIdAndUserId(accountId, currentUserId)
  .orElseThrow(() -> new AccessDeniedException("Not your account"));
```

## Data Protection

### Encryption at Rest
- **Delta API secrets**: AES-256-GCM
- **Database**: Transparent encryption (Cloud SQL)
- **Backups**: Encrypted

### Encryption in Transit
- **All HTTP**: TLS 1.3 (enforced)
- **Database**: SSL mode require
- **Internal services**: mTLS (production)

### Secrets Management
- **Development**: `.env` files (gitignored)
- **Production**: Google Secret Manager / HashiCorp Vault
- **Never in code**: No hardcoded secrets
- **Rotation**: Quarterly for API keys

## API Security

### Rate Limiting
| Endpoint Category | Limit |
|-------------------|-------|
| Auth | 10 req/min |
| Trading | 60 req/min |
| Market Data | 120 req/min |
| WebSocket | 1 conn/user |

### Input Validation
- All inputs validated via Bean Validation
- Sanitization for XSS prevention
- SQL injection prevented by JPA parameters

### CORS
- Configurable allowed origins
- Credentials allowed
- No wildcard with credentials

### Security Headers
```
Content-Security-Policy: default-src 'self'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=()
```

## Multi-User Isolation

### Database Level
- Row-Level Security (RLS) policies
- `current_setting('app.current_user_id')` for context
- All queries scoped to user_id

### Application Level
- Every repository query includes user_id
- Service layer validates ownership
- No cross-user data access possible

### API Level
- JWT contains user_id
- All endpoints extract user from token
- No user_id in path parameters (inferred from token)

## Delta Credentials Security

### Storage
```sql
encrypted_api_key TEXT NOT NULL   -- AES-256-GCM
encrypted_api_secret TEXT NOT NULL -- AES-256-GCM
```

### Encryption Process
1. Generate random 96-bit nonce per encryption
2. Encrypt with AES-256-GCM (authenticated encryption)
3. Store: `nonce + ciphertext + auth_tag` (Base64)
4. Key from Secret Manager (rotated quarterly)

### Access Pattern
1. Spring Boot loads encryption key at startup
2. Decrypt only when submitting order to Delta
3. Never logged, never returned to frontend
4. Frontend sees only: `{ connected: true, environment: "LIVE" }`

## Audit Logging

### Logged Events
- Authentication (login, logout, failed attempts)
- Account changes (create, update, delete)
- Delta connection (connect, disconnect, test)
- Trading (order place, cancel, fill, position open/close)
- Risk (validation pass/fail, overrides)
- Admin actions (user disable, kill switch)

### Log Format
```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "userId": "uuid",
  "action": "ORDER_PLACED",
  "resourceType": "ORDER",
  "resourceId": "uuid",
  "ipAddress": "192.168.1.1",
  "userAgent": "Mozilla/5.0...",
  "success": true,
  "details": { "symbol": "BTCUSD.P", "side": "BUY" }
}
```

### Retention
- Security logs: 7 years
- Trading logs: 7 years (regulatory)
- Debug logs: 30 days

## Frontend Security

### Token Handling
- **No localStorage** for tokens (HttpOnly cookies only)
- **No token exposure** to JavaScript
- **Automatic refresh** via interceptor

### Content Security Policy
```html
<meta http-equiv="Content-Security-Policy"
  content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' wss://api.quantedge.ai;">
```

### XSS Prevention
- React auto-escapes
- No `dangerouslySetInnerHTML`
- Sanitize any user content

## Infrastructure Security

### Network
- **Database**: Private subnet, no public IP
- **Backend**: Private subnet, load balancer only
- **Frontend**: CDN + WAF
- **Python Engine**: Private, internal only

### Kubernetes (Production)
- Pod Security Standards: Restricted
- Network Policies: Deny all, allow specific
- Secrets: External Secrets Operator
- Images: Signed, vulnerability scanned

### Monitoring
- Failed auth alerts (>5/min)
- Unusual trading patterns
- API error rate spikes
- Database connection anomalies

## Incident Response

### Credential Compromise
1. Revoke all user sessions
2. Force password reset
3. Rotate Delta credentials
4. Audit recent activity

### API Key Leak
1. Revoke Delta API key immediately
2. Generate new key for user
3. Audit orders placed with compromised key
4. Notify user

### Data Breach
1. Contain & assess scope
2. Notify affected users (72hrs GDPR)
3. Regulatory notification
4. Post-incident review

## Compliance

- **GDPR**: Right to deletion, data portability
- **SOC 2**: Access controls, audit logs
- **Financial**: Trade retention 7 years
- **PCI DSS**: Not applicable (no card data)