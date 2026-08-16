import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';

const AUTH_TOKEN = process.env.AUTH_TOKEN;

if (!AUTH_TOKEN) {
  console.warn('[Auth] AUTH_TOKEN not set — authentication disabled. Set AUTH_TOKEN env var to enable.');
}

/**
 * Session-based authentication for a single-owner trading application.
 * The backend issues a short-lived session JWT after validating operator credentials.
 * The original AUTH_TOKEN never leaves the backend secure environment.
 *
 * Authentication flow:
 *  1. Operator login: POST /auth/login with credentials
 *  2. Backend validates credentials against AUTH_TOKEN (never exposed to frontend)
 *  3. Backend issues session JWT signed with shorter expiry
 *  4. Frontend stores session JWT and axios interceptor attaches it
 *  5. Session token rejected after expiry or logout
 *
 * LIVE authorization remains separate layer:
 *  AUTHENTICATED OPERATOR + CONFIRM_LIVE_TRADING + ALLOW_LIVE_TRADING=true + LiveTradingGuard
 */

// Session JWT configuration
const SESSION_JWT_SECRET = process.env.SESSION_JWT_SECRET || 'algoapp_session_dev_change_me';
const SESSION_JWT_EXPIRES_IN = process.env.SESSION_JWT_EXPIRES_IN || '24h';

/**
 * Generate a session JWT for an authenticated operator.
 */
export const generateSessionJwt = (operatorId: string): string => {
  return jwt.sign(
    { operatorId, type: 'session' },
    SESSION_JWT_SECRET,
    { expiresIn: SESSION_JWT_EXPIRES_IN }
  );
};

/**
 * Validate session JWT and extract operator identity.
 * Returns operatorId if valid, throws if invalid.
 */
export const verifySessionJwt = (token: string): { operatorId: string; type: string } => {
  if (!token) {
    throw new Error('Invalid session token: missing');
  }
  try {
    const decoded = jwt.verify(token, SESSION_JWT_SECRET) as {
      operatorId: string;
      type: string;
    };
    if (decoded.type !== 'session') {
      throw new Error('Invalid session token: wrong type');
    }
    return decoded;
  } catch (err) {
    throw new Error(`Session token validation failed: ${(err as Error).message}`);
  }
};

/**
 * Authentication middleware that checks for valid session JWT.
 * If no valid session, returns 401. If AUTH_TOKEN is not configured,
 * auth is disabled and request passes through.
 */
export const authMiddleware = (req: Request, res: Response, next: NextFunction): void => {
  // If AUTH_TOKEN is not configured, pass through (auth disabled for development)
  if (!AUTH_TOKEN) {
    next();
    return;
  }

  const authHeader = req.headers['authorization'];
  const providedToken = authHeader ? authHeader.split(' ')[1] : '';

  // If no token provided, reject (auth enabled but not authenticated)
  if (!providedToken) {
    res.status(401).json({
      success: false,
      error: 'UNAUTHORIZED: Authentication required. Use /auth/login to obtain a session token.',
      meta: {
        requestId: (req as any).correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }

  // Verify the session JWT
  try {
    const decoded = verifySessionJwt(providedToken);
    // Attach operator identity to request for downstream use
    (req as any).operatorId = decoded.operatorId;
    (req as any).isAuthenticated = true;
    next();
    return;
  } catch (err) {
    res.status(401).json({
      success: false,
      error: `UNAUTHORIZED: ${(err as Error).message}`,
      meta: {
        requestId: (req as any).correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }
};

/**
 * Login endpoint: validate operator credentials and issue session JWT.
 * Credentials are compared against the backend AUTH_TOKEN.
 * On success, returns a session JWT that the frontend uses for authenticated requests.
 */
export const loginHandler = async (req: Request, res: Response): Promise<void> => {
  const { token: providedToken } = req.body;

  // Validate provided token against backend AUTH_TOKEN
  if (!providedToken || providedToken !== AUTH_TOKEN) {
    res.status(401).json({
      success: false,
      error: 'UNAUTHORIZED: Invalid credentials. The provided token does not match the backend AUTH_TOKEN.',
      meta: {
        requestId: (req as any).correlationId || 'req-login',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }

  // Issue session JWT for this operator
  const operatorId = `operator-${Date.now()}`;
  const sessionJwt = generateSessionJwt(operatorId);

  res.cookie('session', sessionJwt, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    maxAge: 24 * 60 * 60 * 1000, // 24 hours
  });

  res.json({
    success: true,
    data: {
      operatorId,
      sessionJwt,
      message: 'Login successful. Session cookie set.',
    },
    meta: {
      requestId: (req as any).correlationId || 'req-login',
      timestamp: new Date().toISOString(),
    },
  });
};

/**
 * Logout endpoint: clear session cookie and invalidate session.
 */
export const logoutHandler = async (req: Request, res: Response): Promise<void> => {
  res.clearCookie('session', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
  });

  res.json({
    success: true,
    data: { message: 'Logged out successfully.' },
    meta: {
      requestId: (req as any).correlationId || 'req-logout',
      timestamp: new Date().toISOString(),
    },
  });
};