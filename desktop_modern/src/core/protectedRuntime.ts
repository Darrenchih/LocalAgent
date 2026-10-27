import { invoke } from '@tauri-apps/api/core';

const states = ['disabled', 'starting', 'ready', 'stopping', 'stopped', 'failed'] as const;
export type ProtectedRuntimeStatus = {
  state: typeof states[number];
  execution_enabled: false;
  error: string | null;
  bundle_id: string;
  artifact_version: string;
  last_health_ms: number | null;
};
export async function protectedRuntime(action: 'start' | 'status' | 'stop'): Promise<ProtectedRuntimeStatus> {
  const value = await invoke<unknown>(`protected_runtime_${action}`);
  if (!value || typeof value !== 'object') throw new Error('invalid_status');
  const row = value as Record<string, unknown>;
  if (row.execution_enabled !== false || !states.includes(row.state as ProtectedRuntimeStatus['state']) ||
      !(row.error === null || typeof row.error === 'string') || typeof row.bundle_id !== 'string' ||
      typeof row.artifact_version !== 'string' || !(row.last_health_ms === null ||
      (typeof row.last_health_ms === 'number' && Number.isFinite(row.last_health_ms) && row.last_health_ms >= 0))) {
    throw new Error('invalid_status');
  }
  return value as ProtectedRuntimeStatus;
}
