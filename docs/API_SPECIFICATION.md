# API Specification - QuantEdge AI V2

## Overview

REST API with OpenAPI 3.0 contract. All endpoints under `/api/v1`.

## Authentication

- JWT in HttpOnly cookies (access_token, refresh_token)
- Access token: 24 hours
- Refresh token: 7 days
- CSRF protected via SameSite=Lax

## Base URL

```
Development: http://localhost:8080/api/v1
Production:  https://api.quantedge.ai/api/v1
```

## Endpoints

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | /auth/signup | Register new user |
| POST | /auth/login | Login user |
| POST | /auth/logout | Logout (clear cookies) |
| POST | /auth/refresh | Refresh access token |
| GET | /auth/me | Get current user |
| POST | /auth/forgot-password | Request password reset |
| POST | /auth/reset-password | Reset password with token |

#### Signup Request
```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "password": "securePassword123"
}
```

#### Login Request
```json
{
  "email": "john@example.com",
  "password": "securePassword123"
}
```

#### Auth Response
```json
{
  "user": {
    "id": "uuid",
    "name": "John Doe",
    "email": "john@example.com",
    "isActive": true
  },
  "accessToken": "jwt-token"
}
```

### Users

| Method | Path | Description |
|--------|------|-------------|
| GET | /users/me | Get current user profile |
| PATCH | /users/me | Update profile |
| DELETE | /users/me | Delete account |

### Trading Accounts

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts | List user's accounts |
| POST | /accounts | Create trading account |
| GET | /accounts/{id} | Get account details |
| PATCH | /accounts/{id} | Update account |
| DELETE | /accounts/{id} | Delete account |
| GET | /accounts/{id}/balance | Get current balance |
| POST | /accounts/{id}/set-default | Set as default |

### Delta Connections

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts/{id}/delta | Get connection status |
| POST | /accounts/{id}/delta | Connect Delta account |
| DELETE | /accounts/{id}/delta | Disconnect Delta |
| POST | /accounts/{id}/delta/test | Test connection |

#### Connect Request
```json
{
  "environment": "TESTNET",
  "apiKey": "delta-api-key",
  "apiSecret": "delta-api-secret"
}
```

#### Status Response
```json
{
  "connected": true,
  "environment": "TESTNET",
  "lastConnected": "2024-01-15T10:30:00Z"
}
```

### Risk Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts/{id}/risk | Get risk config |
| PATCH | /accounts/{id}/risk | Update risk config |

### Strategy Configuration

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts/{id}/strategy | Get strategy config |
| PATCH | /accounts/{id}/strategy | Update strategy config |

### Trading Controls

| Method | Path | Description |
|--------|------|-------------|
| POST | /accounts/{id}/algo/enable | Enable ALGO |
| POST | /accounts/{id}/algo/disable | Disable ALGO |
| POST | /accounts/{id}/delta/enable | Enable Delta |
| POST | /accounts/{id}/delta/disable | Disable Delta |
| POST | /accounts/{id}/mode/paper | Set PAPER mode |
| POST | /accounts/{id}/mode/live | Set LIVE mode |

### Orders

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts/{id}/orders | List orders (with filters) |
| POST | /accounts/{id}/orders | Place manual order |
| GET | /accounts/{id}/orders/{orderId} | Get order details |
| POST | /accounts/{id}/orders/{orderId}/cancel | Cancel order |

### Positions

| Method | Path | Description |
|--------|------|-------------|
| GET | /accounts/{id}/positions | List open positions |
| GET | /accounts/{id}/positions/{positionId} | Get position details |
| POST | /accounts/{id}/positions/{positionId}/close | Close position |

### Journal

| Method | Path | Description |
|--------|------|-------------|
| GET | /journal | List entries |
| POST | /journal | Create entry |
| GET | /journal/{id} | Get entry |
| PATCH | /journal/{id} | Update entry |
| DELETE | /journal/{id} | Delete entry |

### Analytics

| Method | Path | Description |
|--------|------|-------------|
| GET | /analytics/summary | Account summary |
| GET | /analytics/pnl | P&L over time |
| GET | /analytics/trades | Trade statistics |
| GET | /analytics/drawdown | Drawdown analysis |

### Market Data (Proxy to Python Engine)

| Method | Path | Description |
|--------|------|-------------|
| GET | /market/candles/{symbol} | Get candles |
| GET | /market/structure/{symbol} | Get SMC structure |
| GET | /market/order-blocks/{symbol} | Get order blocks |
| GET | /market/liquidity/{symbol} | Get liquidity levels |

## WebSocket

```
ws://localhost:8080/api/v1/ws?token={access_token}
```

### Events

| Event | Payload |
|-------|---------|
| order.update | Order status change |
| position.update | Position P&L update |
| account.balance | Balance change |
| trade.executed | Trade execution |
| algo.signal | Strategy signal generated |

## Error Responses

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "status": 400,
  "error": "Validation Failed",
  "message": "Invalid request parameters",
  "validationErrors": {
    "email": "must be a valid email"
  }
}
```

## Rate Limiting

- Auth endpoints: 10 req/min
- Trading endpoints: 60 req/min
- Market data: 120 req/min
- WebSocket: 1 connection/user

## Versioning

- URL path versioning: `/api/v1/`
- Breaking changes = new version
- Deprecation notice: 90 days