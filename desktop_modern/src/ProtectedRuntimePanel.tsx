import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { protectedRuntime, type ProtectedRuntimeStatus } from './core/protectedRuntime';

export default function ProtectedRuntimePanel() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<ProtectedRuntimeStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const revision = useRef(0);
  const requests = useRef(0);
  const mutating = useRef(false);
  const request = useCallback(async (action: 'start' | 'status' | 'stop') => {
    const current = ++revision.current;
    requests.current++;
    if (action !== 'status') { mutating.current = true; setBusy(true); }
    try {
      const next = await protectedRuntime(action);
      if (active.current && current === revision.current) { setStatus(next); setFailed(false); }
    } catch {
      if (active.current && current === revision.current) { setStatus(null); setFailed(true); }
    } finally {
      requests.current--;
      if (action !== 'status') { mutating.current = false; if (active.current) setBusy(false); }
    }
  }, []);
  useEffect(() => {
    active.current = true;
    void request('status');
    const timer = setInterval(() => { if (!requests.current) void request('status'); }, 2000);
    return () => { active.current = false; revision.current++; clearInterval(timer); };
  }, [request]);
  const state = status?.state;
  return <div className="protected-runtime-panel">
    <h3>{t('protectedRuntime.title')}</h3>
    <p className="notice">{t('protectedRuntime.managementOnly')} · <strong>{t('protectedRuntime.executionDisabled')}</strong></p>
    <p>{t('protectedRuntime.safety')}</p>
    <p role="status">{t(`protectedRuntime.${state || 'unknown'}`)}</p>
    {failed && <p className="error-banner" role="alert">{t('protectedRuntime.requestError')}</p>}
    {status?.error && <p className="error-banner" role="alert">{t('protectedRuntime.runtimeError')}: <code>{status.error}</code></p>}
    {status && <p>{t('protectedRuntime.bundle')}: {status.bundle_id || '—'} · {t('protectedRuntime.version')}: {status.artifact_version || '—'}</p>}
    <div className="row-actions">
      <button disabled={busy || !state || !['disabled', 'stopped', 'failed'].includes(state)} onClick={() => { if (!mutating.current) void request('start'); }}>{t('protectedRuntime.start')}</button>
      <button disabled={busy} onClick={() => void request('status')}>{t('protectedRuntime.refresh')}</button>
      <button disabled={busy || !state || !['ready', 'starting'].includes(state)} onClick={() => { if (!mutating.current) void request('stop'); }}>{t('protectedRuntime.stop')}</button>
    </div>
  </div>;
}
