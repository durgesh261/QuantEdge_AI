import jwt from 'jsonwebtoken';
import type { Request, Response, NextFunction } from 'express';

declare module 'express' {
  interface Request {
    operatorId?: string;
    isAuthenticated?: boolean;
  }
}

const AUTH_TOKEN = process.env.AUTH_TOKEN;

if (!AUTH_TOKEN) {
  console.warn('[Auth] AUTH_TOKEN not set — authentication disabled. Set AUTH_TOKEN env var to enable.');
}

const SESSION_JWT_SECRET = process.env.SESSION_JWT_SECRET || 'algoapp_session_dev_change_me';

export const generateSessionJwt = (operatorId: string): string => {
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

export const authMiddleware = (req: Request, res: Response, next: NextFunction): void => {
  if (!AUTH_TOKEN) {
    next();
    return;
  }

  const authHeader = req.headers.authorization;
  const providedToken = authHeader ? authHeader.split(' ')[1] : '';

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
        requestId: (req as any).correlationId || 'req-auth',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }
};

export default authMiddleware;