import { Request, Response } from 'express';
import { ExecutionEngineService } from './services/ExecutionEngineService.js';

export class ExecutionController {
  constructor(private executionService: ExecutionEngineService) {}

  public placeOrder = async (req: Request, res: Response): Promise<void> => {
    try {
      // ── F-1 Security: explicit allow-list — isEmergencyClose MUST NOT come from HTTP ──
      const {
        symbol,
        side,
        orderType,
        size,
        price,
        stopPrice,
        leverage,
        reduceOnly,
        postOnly,
        stopLossPrice,
        takeProfitPrice,
        clientOrderId,
      } = req.body;

      const result = await this.executionService.placeOrder({
        symbol,
        side,
        orderType,
        size,
        price,
        stopPrice,
        leverage,
        reduceOnly,
        postOnly,
        stopLossPrice,
        takeProfitPrice,
        clientOrderId,
        // isEmergencyClose is NEVER accepted from HTTP — internal use only
      });
      res.status(result.success ? 200 : 400).json({
        success: result.success,
        data: result,
      });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Execution failed' });
    }
  };

  public validateOrder = async (req: Request, res: Response): Promise<void> => {
    try {
      const result = await this.executionService.validateOrder(req.body);
      res.json({
        success: true,
        data: result,
      });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Validation failed' });
    }
  };

  public cancelOrder = async (req: Request, res: Response): Promise<void> => {
    try {
      const orderId = req.params['id'] || '';
      const result = await this.executionService.cancelOrder(orderId);
      res.json({ success: true, data: result });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Cancel failed' });
    }
  };

  public cancelAllOrders = async (_req: Request, res: Response): Promise<void> => {
    try {
      const result = await this.executionService.cancelAllOrders();
      res.json({ success: true, data: result });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Cancel all failed' });
    }
  };

  public closePosition = async (req: Request, res: Response): Promise<void> => {
    try {
      const symbol = req.params['symbol'] || '';
      const result = await this.executionService.closePosition(symbol);
      res.json({ success: true, data: result });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Close position failed' });
    }
  };

  public modifyOrder = async (req: Request, res: Response): Promise<void> => {
    try {
      const orderId = req.params['id'] || '';
      const result = await this.executionService.modifyOrder(orderId, req.body);
      res.json({ success: true, data: result });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message || 'Modify order failed' });
    }
  };

  public getActiveOrders = async (_req: Request, res: Response): Promise<void> => {
    try {
      const active = this.executionService.getActiveOrders();
      res.json({ success: true, data: active });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message });
    }
  };

  public getExecutionHistory = async (_req: Request, res: Response): Promise<void> => {
    try {
      const history = this.executionService.getExecutionHistory();
      res.json({ success: true, data: history });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message });
    }
  };

  public toggleKillSwitch = async (req: Request, res: Response): Promise<void> => {
    try {
      const { active } = req.body;
      this.executionService.setKillSwitch(!!active);
      res.json({
        success: true,
        data: { killSwitchActive: this.executionService.getKillSwitchStatus() },
      });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err?.message });
    }
  };
}
