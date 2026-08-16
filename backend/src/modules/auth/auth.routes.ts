import { Router } from 'express';
import { loginHandler, logoutHandler, getMeHandler } from './auth.controller.js';

const router = Router();

router.post('/login', loginHandler);
router.post('/logout', logoutHandler);
router.get('/me', getMeHandler);

export const authRouter = router;