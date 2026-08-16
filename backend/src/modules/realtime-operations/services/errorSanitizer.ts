/**
 * Safe error sanitizer that removes sensitive data from error objects
 * before they are stored in IncidentHistory, sent in notifications,
 * or published to the AppEventBus.
 *
 * Never stores or transmits:
 * - error.stack (full stack trace)
 * - request headers
 * - authorization headers
 * - Bearer tokens
 * - passwords
 * - API keys (DELTA_API_KEY, DELTA_API_SECRET, TELEGRAM_BOT_TOKEN, etc.)
 * - private credentials
 *
 * Only safe, user-facing information is retained.
 */
export class ErrorSanitizer {
  /**
   * Sanitize an error object, returning a safe message string.
   * The original error object is not modified.
   */
  public static sanitizeError(error: unknown): { message: string; name: string } {
    let message: string;
    let name: string;

    if (error instanceof Error) {
      // Safe: only keep the base message, strip stack trace
      name = error.name;
      message = this.safeString(error.message);
    } else if (error !== null && typeof error === 'object' && 'message' in error) {
      // Generic object with a message property - extract safely
      name = (error as { name?: string }).name || 'Error';
      message = this.safeString(String((error as { message: unknown }).message));
    } else {
      // Primitive value or null/undefined
      name = typeof error === 'string' ? error : 'Error';
      message = this.safeString(String(error));
    }

    return { message, name };
  }

  /**
   * Sanitize a string, removing potential credential-like patterns.
   * Replaces patterns that look like:
   * - API keys (long alphanumeric strings)
   * - Bearer tokens
   * - Authorization headers
   * - Passwords (key=value, redact the value only, keep the key)
   * - Telegram bot tokens
   */
  private static safeString(str: string): string {
    let result = str;

    // Remove potential DELTA_API_SECRET patterns (32+ char alphanumeric)
    result = result.replace(/[a-zA-Z0-9]{32,}/g, '[REDACTED]');

    // Remove potential DELTA_API_KEY patterns
    result = result.replace(/key[=:\s]+[a-zA-Z0-9]{20,}/gi, 'key=[REDACTED]');

    // Remove potential Bearer token patterns
    result = result.replace(/Authorization:\s*Bearer\s+[a-zA-Z0-9\-._~+\/]+/gi, 'Authorization: Bearer [REDACTED]');

    // Remove potential Telegram Bot Token patterns (format: ID:TOKEN)
    // Format: numeric ID followed by colon, then the token
    result = result.replace(/\d{3,5}:[a-zA-Z0-9\-._~+\/]{35}/g, '[TELEGRAM_TOKEN_REDACTED]');

    // Remove password-like patterns (key=value, redact the value only, keep the key)
    result = result.replace(/(?<=password=)[^\s]+/gi, '[REDACTED]');

    // Remove Authorization header patterns (entire line)
    result = result.replace(/^Authorization:\s.*$/m, 'Authorization: [REDACTED]');

    // General: any long alphanumeric string after api_key, secret, or token =
    result = result.replace(/(?:^|\\s)(?:api[_-]?key|secret|token)=[^\s"]+/gi, match => {
      // Replace just the value part after the =
      const valuePart = match.match(/=[^\s"]+$/);
      return valuePart ? match.replace(valuePart[0], '[REDACTED]') : match;
    });

    return result;
  }
}

export default ErrorSanitizer;