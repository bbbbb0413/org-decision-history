import { useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  INDEX_STAGE_LABEL, INDEX_TRIGGER_LABEL, JOB_STATUS_LABEL,
  type IndexJob, type IndexJobItem, type JobStatus,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const STATUS_TONE: Record<JobStatus, 'neutral' | 'accent' | 'ok' | 'err'> = {
  PENDING: 'neutral', RUNNING: 'accent', COMPLETED: 'ok', FAILED: 'err',
};

/** 색인 작업 현황. 시스템 관리자만 본다. */
export function IndexJobPanel() {
  const [jobs, setJobs] = useState<IndexJob[]>([]);
  const [selected, setSelected] = useState<IndexJob | null>(null);
  const [items, setItems] = useState<IndexJobItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const page = await api.indexJobs({ size: 20 });
      setJobs(page.content);
      // 선택 중인 작업이 있으면 최신 상태로 갈아 끼운다
      setSelected((prev) => (prev ? page.content.find((j) => j.jobKey === prev.jobKey) ?? prev : null));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '작업 목록을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // 처리 중인 작업이 있는 동안만 주기적으로 갱신한다. 끝났는데 계속 물어보지 않는다.
  useEffect(() => {
    const running = jobs.some((j) => j.status === 'RUNNING' || j.status === 'PENDING');
    if (!running) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [jobs, load]);

  async function openJob(job: IndexJob) {
    setSelected(job);
    try {
      setItems(await api.indexJobItems(job.jobKey));
    } catch {
      setItems([]);
    }
  }

  async function runBulk() {
    setBusy(true);
    setError(null);
    try {
      await api.runBulkIndex();
      await load();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '일괄 색인을 실행하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  async function retry(item: IndexJobItem) {
    if (!selected) return;
    try {
      await api.retryIndexItem(selected.jobKey, item.id);
      await openJob(selected);
      await load();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '재처리하지 못했습니다.');
    }
  }

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <div>
          <h2 style={styles.title}>색인 작업</h2>
          <p style={styles.sub}>
            문서를 등록·수정하면 자동으로 색인된다. 아래 일괄 색인은 전체 재색인용이다.
          </p>
        </div>
        <Button variant="primary" onClick={runBulk} disabled={busy} style={{ marginLeft: 'auto' }}>
          {busy ? '실행 중…' : '일괄 색인 실행'}
        </Button>
      </header>

      {error && <div style={styles.error}>{error}</div>}

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>jobKey</th>
            <th style={styles.th}>트리거</th>
            <th style={styles.th}>대상</th>
            <th style={{ ...styles.th, width: 160 }}>진행률</th>
            <th style={styles.th}>시작</th>
            <th style={styles.th}>상태</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.jobKey} onClick={() => openJob(job)}
                style={{ cursor: 'pointer', background: selected?.jobKey === job.jobKey ? 'var(--accent-bg)' : undefined }}>
              <td style={{ ...styles.td, fontFamily: 'var(--mono)', fontSize: 11 }}>{job.jobKey.slice(0, 8)}</td>
              <td style={styles.td}>{INDEX_TRIGGER_LABEL[job.triggerType] ?? job.triggerType}</td>
              <td style={styles.td}>{job.totalCount}건</td>
              <td style={styles.td}>
                <div style={styles.bar}><span style={{ ...styles.barFill, width: `${job.progress * 100}%` }} /></div>
                <div style={styles.counts}>
                  성공 {job.successCount} · 실패 {job.failCount} · 대기 {job.pendingCount}
                </div>
              </td>
              <td style={styles.td}>{new Date(job.requestedAt).toLocaleTimeString('ko-KR')}</td>
              <td style={styles.td}>
                <Badge tone={STATUS_TONE[job.status]}>{JOB_STATUS_LABEL[job.status]}</Badge>
              </td>
            </tr>
          ))}
          {jobs.length === 0 && (
            <tr><td colSpan={6} style={styles.empty}>색인 작업이 없습니다. 문서를 등록하면 자동으로 생깁니다.</td></tr>
          )}
        </tbody>
      </table>

      {selected && (
        <div style={styles.detail}>
          <div style={styles.detailLabel}>
            문서별 처리 상태 — {selected.jobKey.slice(0, 8)}
          </div>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>문서</th>
                <th style={styles.th}>실패 단계</th>
                <th style={styles.th}>사유</th>
                <th style={styles.th}>재시도</th>
                <th style={styles.th}>상태</th>
                <th style={styles.th} />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td style={styles.td}>{item.documentTitle ?? `#${item.documentId}`} <span style={styles.ver}>v{item.documentVersion}</span></td>
                  <td style={styles.td}>{item.stage ? INDEX_STAGE_LABEL[item.stage] ?? item.stage : '—'}</td>
                  <td style={{ ...styles.td, color: item.failureReason ? 'var(--err)' : undefined }}>
                    {item.failureReason ?? '—'}
                  </td>
                  <td style={styles.td}>{item.retryCount} / 3</td>
                  <td style={styles.td}><Badge tone={STATUS_TONE[item.status]}>{JOB_STATUS_LABEL[item.status]}</Badge></td>
                  <td style={{ ...styles.td, textAlign: 'right' }}>
                    {item.status === 'FAILED' && item.retryable && (
                      <Button size="sm" onClick={() => retry(item)}>다시 처리</Button>
                    )}
                    {item.status === 'FAILED' && !item.retryable && (
                      <span style={styles.noRetry}>재시도 대상 아님</span>
                    )}
                  </td>
                </tr>
              ))}
              {items.length === 0 && <tr><td colSpan={6} style={styles.empty}>항목이 없습니다.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'flex-start', gap: 16, padding: '16px 18px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  title: { margin: 0, fontSize: 15, fontWeight: 700 },
  sub: { margin: '4px 0 0', fontSize: 12.5, color: 'var(--ink-3)', maxWidth: '56ch' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '10px 14px', borderBottom: '1px solid var(--rule-soft)', verticalAlign: 'middle' },
  bar: { height: 5, background: 'var(--rule-soft)', borderRadius: 3, overflow: 'hidden' },
  barFill: { display: 'block', height: '100%', background: 'var(--accent)', transition: 'width 200ms' },
  counts: { fontSize: 10.5, color: 'var(--ink-3)', marginTop: 3 },
  ver: { color: 'var(--ink-3)', fontSize: 11 },
  detail: { borderTop: '1px solid var(--rule)', background: '#fbfcfd' },
  detailLabel: { padding: '12px 18px 0', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.06em' },
  noRetry: { fontSize: 11, color: 'var(--ink-3)' },
  empty: { padding: 28, textAlign: 'center', color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
};
