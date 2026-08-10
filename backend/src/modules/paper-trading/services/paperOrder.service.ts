import {
  PaperOrderDto,
  PaperOrderStatus,
  PaperOrderType,
  PaperOrderSide,
  CreatePaperOrderInput,
  ModifyPaperOrderInput,
  PaperJournalEventType,
} from '@algoapp/shared';

import { PaperRiskService } from './paperRisk.service.js';
import { PaperPositionService } from './paperPosition.service.js';
import { PaperJournalService } from './paperJournal.service.js';
import { candleEngine } from '../../../engine/CandleEngine.js';

let openOrders: PaperOrderDto[] = [];

export class PaperOrderService {
  public static async getOrders(): Promise<PaperOrderDto[]> {
    return openOrders;
  }

  public static async createOrder(input: CreatePaperOrderInput): Promise<PaperOrderDto> {
    // Get LIVE price — fallback to explicit input.price if provided
    const liveCandle = candleEngine.getLiveCandle(input.symbol, '1H');
    const livePrice = input.price || liveCandle?.close || 0;
    
    if (!livePrice || livePrice <= 0) {
      throw new Error(`Cannot create paper order: no live price available for ${input.symbol}`);
    }

    const executionPrice = input.price || livePrice;
    const notional = executionPrice * input.quantity;

    const activePositions = await PaperPositionService.getOpenPositions();
    
    // Evaluate Risk Gate
    const riskCheck = await PaperRiskService.validateOrderRisk(input.symbol, notional, activePositions.length);
    if (!riskCheck.passed) {
      await PaperJournalService.logEntry(
        PaperJournalEventType.RISK_EVENT,
        'PAPER_ORDER_REJECTED',
        riskCheck.reason || 'Risk policy evaluation failed',
        input.symbol
      );
      throw new Error(riskCheck.reason || 'Order rejected by risk engine policy.');
    }

    const isMarket = input.orderType === PaperOrderType.MARKET;

    const order: PaperOrderDto = {
      id: `PORD-${Date.now()}`,
      symbol: input.symbol,
      side: input.side,
      orderType: input.orderType,
      price: input.price,
      stopPrice: input.stopPrice,
      quantity: input.quantity,
      filledQuantity: isMarket ? input.quantity : 0.0,
      status: isMarket ? PaperOrderStatus.FILLED : PaperOrderStatus.PENDING,
      stopLoss: input.stopLoss,
      takeProfit: input.takeProfit,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    if (isMarket) {
      await PaperPositionService.openPosition(
        input.symbol,
        input.side === PaperOrderSide.BUY
          ? (PaperOrderSide.BUY as unknown as any)
          : (PaperOrderSide.SELL as unknown as any),
        executionPrice,
        input.quantity,
        input.leverage || 10,
        input.stopLoss,
        input.takeProfit
      );

      await PaperJournalService.logEntry(
        PaperJournalEventType.ORDER_FILL,
        'PAPER_MARKET_FILL',
        `Executed ${input.side} ${input.quantity} ${input.symbol} @ $${executionPrice} (LIVE)`,
        input.symbol
      );
    } else {
      openOrders.unshift(order);
      await PaperJournalService.logEntry(
        PaperJournalEventType.ORDER_FILL,
        'PAPER_ORDER_PLACED',
        `Placed ${input.orderType} ${input.side} ${input.quantity} ${input.symbol}`,
        input.symbol
      );
    }

    return order;
  }

  public static async cancelOrder(orderId: string): Promise<PaperOrderDto | null> {
    const index = openOrders.findIndex((o) => o.id === orderId);
    if (index === -1) return null;

    const [cancelledOrder] = openOrders.splice(index, 1);
    if (!cancelledOrder) return null;

    cancelledOrder.status = PaperOrderStatus.CANCELLED;
    cancelledOrder.updatedAt = new Date().toISOString();

    await PaperJournalService.logEntry(
      PaperJournalEventType.ORDER_CANCEL,
      'PAPER_ORDER_CANCELLED',
      `Cancelled paper order ${orderId} for ${cancelledOrder.symbol}`,
      cancelledOrder.symbol
    );

    return cancelledOrder;
  }

  public static async modifyOrder(
    orderId: string,
    input: ModifyPaperOrderInput
  ): Promise<PaperOrderDto | null> {
    const order = openOrders.find((o) => o.id === orderId);
    if (!order) return null;

    if (input.price !== undefined) order.price = input.price;
    if (input.stopPrice !== undefined) order.stopPrice = input.stopPrice;
    if (input.quantity !== undefined) order.quantity = input.quantity;
    if (input.stopLoss !== undefined) order.stopLoss = input.stopLoss;
    if (input.takeProfit !== undefined) order.takeProfit = input.takeProfit;

    order.updatedAt = new Date().toISOString();

    await PaperJournalService.logEntry(
      PaperJournalEventType.ORDER_FILL,
      'PAPER_ORDER_MODIFIED',
      `Modified paper order ${orderId} parameters`,
      order.symbol
    );

    return order;
  }
}
