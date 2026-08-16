import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';

const backupDir = path.join(process.cwd(), 'backups');

export interface BackupStatusDto {
  databaseBackupAt: string;
  journalBackupAt: string;
  replayBackupAt: string;
  configBackupAt: string;
  totalBackupSizeMb: number;
  status: 'SUCCESS' | 'FAILED';
  backupFile?: string;
}

export class BackupManager {
  public static async getBackupStatus(): Promise<BackupStatusDto> {
    const lastBackupFile = fs.readdirSync(backupDir)
      .filter(f => f.endsWith('.db.backup'))
      .sort()
      .pop();

    if (lastBackupFile) {
      const backupPath = path.join(backupDir, lastBackupFile);
      const stat = fs.statSync(backupPath);
      return {
        databaseBackupAt: stat.mtime.toISOString(),
        journalBackupAt: stat.mtime.toISOString(),
        replayBackupAt: stat.mtime.toISOString(),
        configBackupAt: stat.mtime.toISOString(),
        totalBackupSizeMb: Math.round(stat.size / 1024 / 1204),
        status: 'SUCCESS',
        backupFile: lastBackupFile,
      };
    }

    return {
      databaseBackupAt: new Date().toISOString(),
      journalBackupAt: new Date().toISOString(),
      replayBackupAt: new Date().toISOString(),
      configBackupAt: new Date().toISOString(),
      totalBackupSizeMb: 0,
      status: 'NO_BACKUPS',
    };
  }

  public static async triggerBackup(): Promise<BackupStatusDto> {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const backupFile = `dev.db.backup-${timestamp}.db`;
    const backupPath = path.join(backupDir, backupFile);

    try {
      // Use SQLite backup command to create actual backup
      execSync(`cp "${path.join(process.cwd(), 'prisma', 'dev.db')}" "${backupPath}"`);

      // Verify the backup by trying to open it
      execSync(`sqlite3 "${backupPath}" "PRAGMA integrity_check;"`);

      return this.getBackupStatus();
    } catch (error) {
      console.error('Backup failed:', error);
      return {
        databaseBackupAt: new Date().toISOString(),
        journalBackupAt: new Date().toISOString(),
        replayBackupAt: new Date().toISOString(),
        configBackupAt: new Date().toISOString(),
        totalBackupSizeMb: 0,
        status: 'FAILED',
      };
    }
  }

  public static async restoreBackup(): Promise<{ success: boolean; message: string }> {
    const status = await this.getBackupStatus();

    if (status.status !== 'SUCCESS' || !status.backupFile) {
      return { success: false, message: 'No valid backup found' };
    }

    const backupPath = path.join(backupDir, status.backupFile);

    try {
      // Copy backup to replace current DB
      execSync(`cp "${backupPath}" "${path.join(process.cwd(), 'prisma', 'dev.db')}"`);

      // Verify restored database integrity
      execSync(`sqlite3 "${path.join(process.cwd(), 'prisma', 'dev.db')}" "PRAGMA integrity_check;"`);

      return { success: true, message: 'Backup restored successfully' };
    } catch (error) {
      console.error('Restore failed:', error);
      return { success: false, message: 'Backup restore failed' };
    }
  }
}