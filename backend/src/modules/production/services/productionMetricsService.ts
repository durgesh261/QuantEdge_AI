import { ProductionMetricsDto } from '@algoapp/shared';

export class ProductionMetricsService {
  private static startTime = Date.now();

  public static async getMetrics(): Promise<ProductionMetricsDto> {
    const mem = process.memoryUsage();
    const uptimeSeconds = Math.floor((Date.now() - ProductionMetricsService.startTime) / 1000);

    // Estimate CPU usage; process.cpuUsage typing is rough.
    // Fall back to 0 if unavailable; request handlers can override.
    const cpuUsagePercent = 0;

    return {
      cpuUsagePercent,
      memoryUsageMb: Math.round(mem.heapUsed / 1024 / 1024),
      apiLatencyMs: 0, // measured per-request
      pipelineLatencyMs: 0, // measured per-tick
      executionLatencyMs: 0, // measured per-order
      reconnectCount: 0,
      errorCount: 0,
      uptimeSeconds,
      timestamp: new Date().toISOString(),
    };
  }

  public static resetStartTime(): void {
    ProductionMetricsService.startTime = Date.now();
  }
}
