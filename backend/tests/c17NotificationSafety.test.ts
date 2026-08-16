// eslint-disable-next-line @typescript-eslint/no-var-requires
const { ErrorSanitizer } = require('../src/modules/realtime-operations/services/errorSanitizer');

describe('C.17.1 Notification Safety - ErrorSanitizer', () => {
  describe('ErrorSanitizer exists', () => {
    it('ErrorSanitizer is defined', () => {
      // ErrorSanitizer is a class (function type in JS), not undefined
      expect(ErrorSanitizer).toBeDefined();
    });

    it('ErrorSanitizer.safeString exists', () => {
      expect(ErrorSanitizer.safeString).toBeDefined();
    });

    it('ErrorSanitizer.sanitizeError exists', () => {
      expect(ErrorSanitizer.sanitizeError).toBeDefined();
    });
  });

  describe('sanitizeError()', () => {
    it('should sanitize error with DELTA_API_KEY', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'Delta API key: txkBPYQiVlZIAiCbwx9UzWhhqHAlg5',
        name: 'TestError',
      });

      expect(result.message).not.toContain('txkBPYQiVlZIAiCbwx9UzWhhqHAlg5');
      expect(result.message).not.toContain('DELTA_API_KEY');
    });

    it('should sanitize error with DELTA_API_SECRET', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'Delta API secret: uWIKHspcDAdmtEpm39ITi0qygx1igEgOXfcsb31akY7UuG975LtEmJ9gd5iy',
        name: 'TestError',
      });

      expect(result.message).not.toContain('uWIKHspcDAdmtEpm39ITi0qygx1igEgOXfcsb31akY7UuG975LtEmJ9gd5iy');
      expect(result.message).not.toContain('DELTA_API_SECRET');
    });

    it('should sanitize error with Authorization/Bearer token', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123...',
        name: 'TestError',
      });

      expect(result.message).not.toContain('Authorization: Bearer');
      expect(result.message).not.toContain('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9');
    });

    it('should sanitize error with password value removed', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'password=mysecretpassword123',
        name: 'TestError',
      });

      // The word "password" remains but the value is redacted
      expect(result.message).toContain('password=[REDACTED]');
      expect(result.message).not.toContain('mysecretpassword123');
    });

    it('should sanitize error with token value removed', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'token=abc123def456...',
        name: 'TestError',
      });

      // The "token" key remains but the value is redacted
      expect(result.message).toContain('token[REDACTED]');
      expect(result.message).not.toContain('abc123def456');
    });

    it('should sanitize error with secret value removed', () => {
      const result = ErrorSanitizer.sanitizeError({
        message: 'secret=myverysecretkey',
        name: 'TestError',
      });

      // The "secret" key remains but the value is redacted
      expect(result.message).toContain('secret[REDACTED]');
      expect(result.message).not.toContain('myverysecretkey');
    });
  });

  describe('safeString()', () => {
    it('should remove long alphanumeric strings that look like API secrets', () => {
      const result = ErrorSanitizer.safeString(
        'Delta API key: txkBPYQiVlZIAiCbwx9UzWhhqHAlg5 secret: uWIKHspcDAdmtEpm39ITi0qygx1igEgOXfcsb31akY7UuG975LtEmJ9gd5iy'
      );

      expect(result).not.toContain('txkBPYQiVlZIAiCbwx9UzWhhqHAlg5');
      expect(result).not.toContain('uWIKHspcDAdmtEpm39ITi0qygx1igEgOXfcsb31akY7UuG975LtEmJ9gd5iy');
    });

    it('should remove Bearer token patterns', () => {
      const result = ErrorSanitizer.safeString(
        'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123...'
      );

      expect(result).not.toContain('Authorization: Bearer');
      expect(result).not.toContain('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9');
    });

    it('should remove password patterns', () => {
      const result = ErrorSanitizer.safeString('password=mysecretpassword123');

      // The word "password" remains but the value is redacted
      expect(result).toContain('password=[REDACTED]');
      expect(result).not.toContain('mysecretpassword123');
    });

    it('should remove Telegram bot token patterns', () => {
      const result = ErrorSanitizer.safeString(
        'bot token: 123456:ABC-DEF-ghiJklMNOPqrSTUV_wxYzAbC1234'
      );

      expect(result).not.toContain('123456:ABC-DEF-ghiJklMNOPqrSTUV_wxYzAbC1234');
    });
  });
});