import { ShadowDecisionRecordDto } from '@algoapp/shared';

let decisionRecordsStore: ShadowDecisionRecordDto[] = [];

export class DecisionRecorderService {
  public async recordDecision(record: ShadowDecisionRecordDto): Promise<ShadowDecisionRecordDto> {
    decisionRecordsStore.unshift(record);
    return record;
  }

  public async getRecentDecisions(): Promise<ShadowDecisionRecordDto[]> {
    return decisionRecordsStore;
  }
}
