import { Request, Response } from 'express';
import { authMiddleware } from '../../middleware/authenticate.js';
import { verifySessionJwt, generateSessionJwt } from '../../middleware/authenticate.js';

export const loginHandler = async (req: Request, res: Response): Promise<void> => {
  const { token: providedToken } = req.body;

  // Validate provided token against backend AUTH_TOKEN
  if (!providedToken || providedToken !== process.env.AUTH_TOKEN) {
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

  // Set httpOnly session cookie
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

export const getMeHandler = async (req: Request, res: Response): Promise<void> => {
  if (!req.isAuthenticated || !req.operatorId) {
    res.status(401).json({
      success: false,
      error: 'UNAUTHORIZED: No active session.',
      meta: {
        requestId: (req as any).correlationId || 'req-me',
        timestamp: new Date().toISOString(),
      },
    });
    return;
  }

  res.json({
    success: true,
    data: {
      operatorId: req.operatorId,
      authenticated: true,
    },
    meta: {
      requestId: (req as any).correlationId || 'req-me',
      timestamp: new Date().toISOString(),
    },
  });
};