import jwt from 'jsonwebtoken';
import type { Request, Response, NextFunction } from 'express';

declare module 'express' {
  interface Request {
    operatorId?: string;
    isAuthenticated?: boolean;
    correlationId?: string;
  }
}

const AUTH_TOKEN = process.env.AUTH_TOKEN;

if (!AUTH_TOKEN) {
  console.warn('[Auth] AUTH_TOKEN not set — authentication disabled. Set AUTH_TOKEN env var to enable.');
}

const SESSION_JWT_SECRET = process.env.SESSION_JWT_SECRET;
if (!SESSION_JWT_SECRET) {
  throw new Error('SESSION_JWT_SECRET environment variable is not set');
}

export interface JwtPayload {
  operatorId: string;
  type: string;
}

export const generateSessionJwt = (operatorId: string): string => {
  // Use string literal for TypeScript type compatibility; env var used at runtime
  return jwt.sign(
    { operatorId, type: 'session' },
    SESSION_JWT_SECRET,
    { expiresIn: '24h' } as const
  );
};

export const verifySessionJwt = (token: string): { operatorId: string; type: string } => {
  if (!token) {
    throw new Error('Invalid session token: missing');
  }
  try {
    const decoded = jwt.verify(token, SESSION_JWT_SECRET) as JwtPayload;
    if (decoded.type !== 'session') {
      throw new Error('Invalid session token: wrong type');
    }
    return { operatorId: decoded.operatorId, type: decoded.type };
  } catch (err) {
    throw new Error(`Session token validation failed: ${(err as Error).message}`);
  }
};

export const authMiddleware = (req: Request, res: Response, next: NextFunction): void => {
  if (!AUTH_TOKEN) {
    res.status(503).json({
      success: false,
      error: 'SERVICE UNAVAILABLE: Authentication not configured. Set AUTH_TOKEN env var.',
      meta: {
        requestId: req.correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }

  const authHeader = req.headers.authorization;
  const providedToken = authHeader ? authHeader.split(' ')[1] : '';

  if (!providedToken) {
    res.status(401).json({
      success: false,
      error: 'UNAUTHORIZED: Authentication required. Use /auth/login to obtain a session token.',
      meta: {
        requestId: req.correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }

  try {
    const decoded = verifySessionJwt(providedToken);
    req.operatorId = decoded.operatorId;
    req.isAuthenticated = true;
    next();
    return;
  } catch (err) {
    res.status(401).json({
      success: false,
      error: `UNAUTHORIZED: ${(err as Error).message}`,
      meta: {
        requestId: req.correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }
};

export default authMiddleware;