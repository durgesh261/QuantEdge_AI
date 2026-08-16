import { IncidentType, IncidentStatus, IncidentDeliveryStatus } from '../src/modules/realtime-operations/services/incidentHistory.service';
import { NotificationSeverity } from '@algoapp/shared';

describe('C.17.1 Incident History Enums', () => {
  describe('IncidentStatus', () => {
    it('OPEN should be a valid status', () => {
      expect(IncidentStatus.OPEN).toBe('OPEN');
    });

    it('RESOLVED should be a valid status', () => {
      expect(IncidentStatus.RESOLVED).toBe('RESOLVED');
    });
  });

  describe('IncidentDeliveryStatus', () => {
    it('LOCAL_ONLY should be a valid delivery status', () => {
      expect(IncidentDeliveryStatus.LOCAL_ONLY).toBe('LOCAL_ONLY');
    });

    it('SENT should be a valid delivery status', () => {
      expect(IncidentDeliveryStatus.SENT).toBe('SENT');
    });

    it('FAILED should be a valid delivery status', () => {
      expect(IncidentDeliveryStatus.FAILED).toBe('FAILED');
    });
  });

  describe('IncidentType', () => {
    it('SCANNER_HEARTBEAT_STALE should be a valid type', () => {
      expect(IncidentType.SCANNER_HEARTBEAT_STALE).toBe('SCANNER_HEARTBEAT_STALE');
    });

    it('LIVE_AUTHORIZATION_REJECTED should be a valid type', () => {
      expect(IncidentType.LIVE_AUTHORIZATION_REJECTED).toBe('LIVE_AUTHORIZATION_REJECTED');
    });

    it('STARTUP_FAILURE should be a valid type', () => {
      expect(IncidentType.STARTUP_FAILURE).toBe('STARTUP_FAILURE');
    });
  });
});