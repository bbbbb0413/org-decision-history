import { useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import type { AccessPolicy, AuditEntry } from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const SUBJECT_LABEL: Record<string, string> = { USER: '사용자', ROLE: '역할', TEAM: '팀' };
const SCOPE_LABEL: Record<string, string> = { ORGANIZATION: '조직', TEAM: '팀', DOCUMENT: '문서' };
const ACTION_LABEL: Record<string, string> = {
  DOCUMENT_VIEW: '문서 조회',
  DOCUMENT_SEARCH: '검색·질의응답',
  FILE_DOWNLOAD_ISSUE: '원본 다운로드 발급',
  POLICY_CHANGE: '정책 변경',
  ROLE_CHANGE: '소속·역할 변경',
};

/** 접근 정책과 감사 로그. */
export function PolicyPanel() {
  const [tab, setTab] = useState<'policy' | 'audit'>('policy');
  const [policies, setPolicies] = useState<AccessPolicy[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      if (tab === 'policy') setPolicies((await api.policies({ size: 50 })).content);
      else setAudit((await api.auditLogs({ size: 50 })).content);
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '불러오지 못했습니다.');
    }
  }

  useEffect(() => { void load(); }, [tab]);

  async function revoke(id: number) {
    if (!window.confirm('이 정책을 회수할까요? 새 요청부터 적용됩니다.')) return;
    try {
      await api.revokePolicy(id);
      await load();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '회수하지 못했습니다.');
    }
  }

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <button onClick={() => setTab('policy')} style={tabStyle(tab === 'policy')}>접근 정책</button>
        <button onClick={() => setTab('audit')} style={tabStyle(tab === 'audit')}>감사 로그</button>
        <Button size="sm" onClick={load} style={{ marginLeft: 'auto' }}>새로 고침</Button>
      </header>

      {error && <div style={styles.error}>{error}</div>}

      {tab === 'policy' ? (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>주체</th>
              <th style={styles.th}>주체 유형</th>
              <th style={styles.th}>대상 범위</th>
              <th style={styles.th}>대상</th>
              <th style={styles.th}>조회</th>
              <th style={styles.th} />
            </tr>
          </thead>
          <tbody>
            {policies.map((p) => (
              <tr key={p.id}>
                <td style={styles.td}><b>{p.subjectName}</b></td>
                <td style={styles.td}>{SUBJECT_LABEL[p.subjectType]}</td>
                <td style={styles.td}>{SCOPE_LABEL[p.scopeType]}</td>
                <td style={styles.td}>{p.scopeName}</td>
                <td style={styles.td}>
                  <Badge tone={p.canRead ? 'ok' : 'err'}>{p.canRead ? '허용' : '차단'}</Badge>
                </td>
                <td style={{ ...styles.td, textAlign: 'right' }}>
                  {!p.revokedAt && <Button size="sm" onClick={() => revoke(p.id)}>회수</Button>}
                </td>
              </tr>
            ))}
            {policies.length === 0 && <tr><td colSpan={6} style={styles.empty}>정책이 없습니다.</td></tr>}
          </tbody>
        </table>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>시각</th>
              <th style={styles.th}>요청자</th>
              <th style={styles.th}>동작</th>
              <th style={styles.th}>대상</th>
              <th style={styles.th}>결과</th>
              <th style={styles.th}>추적 ID</th>
            </tr>
          </thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td style={styles.td}>{new Date(a.occurredAt).toLocaleString('ko-KR')}</td>
                <td style={styles.td}>{a.actorName ?? '—'}</td>
                <td style={styles.td}>{ACTION_LABEL[a.action] ?? a.action}</td>
                <td style={styles.td}>{a.reason ?? (a.targetId ? `#${a.targetId}` : '—')}</td>
                <td style={styles.td}>
                  <Badge tone={a.result === 'ALLOWED' ? 'ok' : 'err'}>
                    {a.result === 'ALLOWED' ? '허용' : '차단'}
                  </Badge>
                </td>
                <td style={{ ...styles.td, fontFamily: 'var(--mono)', fontSize: 11 }}>{a.correlationId ?? '—'}</td>
              </tr>
            ))}
            {audit.length === 0 && <tr><td colSpan={6} style={styles.empty}>기록이 없습니다.</td></tr>}
          </tbody>
        </table>
      )}
    </section>
  );
}

function tabStyle(active: boolean): React.CSSProperties {
  return {
    padding: '8px 14px', fontSize: 13, fontWeight: 700, background: 'transparent',
    border: 'none', borderBottom: `2px solid ${active ? 'var(--ink)' : 'transparent'}`,
    color: active ? 'var(--ink)' : 'var(--ink-3)', marginBottom: -1, borderRadius: 0,
  };
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'center', gap: 4, padding: '4px 14px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '9px 14px', borderBottom: '1px solid var(--rule-soft)' },
  empty: { padding: 32, textAlign: 'center', color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
};
