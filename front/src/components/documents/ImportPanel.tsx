import { useEffect, useRef, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  DOCUMENT_TYPE_LABEL, JOB_STATUS_LABEL,
  type CurrentUser, type DocumentType, type ImportItem, type ImportJobDetail,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Field } from '../ui/Field';

interface Props {
  user: CurrentUser;
  onDone: () => void;
}

const TYPES = Object.keys(DOCUMENT_TYPE_LABEL) as DocumentType[];

/** 문서 일괄 가져오기. 일부 파일이 실패해도 나머지는 등록된다. */
export function ImportPanel({ user, onDone }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [documentType, setDocumentType] = useState<DocumentType>('MEETING_NOTE');
  const [teamId, setTeamId] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  const [job, setJob] = useState<ImportJobDetail | null>(null);
  const [retrying, setRetrying] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function submit() {
    if (files.length === 0) {
      setFieldErrors({ files: '가져올 파일을 한 개 이상 선택해 주세요.' });
      return;
    }
    if (!user.organizationId) {
      setError('소속 조직이 없어 가져오기를 실행할 수 없습니다.');
      return;
    }
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const created = await api.createImport(files, documentType, user.organizationId, teamId ? Number(teamId) : null);
      const detail = await api.getImport(created.jobKey);
      setJob(detail);
    } catch (e) {
      if (e instanceof HttpError) {
        setError(e.body.message);
        setFieldErrors(Object.fromEntries((e.body.details ?? []).map((d) => [d.field, d.message])));
      } else {
        setError('가져오기를 실행하지 못했습니다.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  // 처리 중인 동안만 주기적으로 갱신한다
  useEffect(() => {
    if (!job || (job.status !== 'PENDING' && job.status !== 'RUNNING')) return;
    const timer = setInterval(async () => {
      try {
        setJob(await api.getImport(job.jobKey));
      } catch { /* 다음 주기에 다시 시도한다 */ }
    }, 1500);
    return () => clearInterval(timer);
  }, [job]);

  async function retry() {
    if (!job) return;
    setRetrying(true);
    setError(null);
    try {
      await api.retryImport(job.jobKey);
      setJob(await api.getImport(job.jobKey));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '재시도하지 못했습니다.');
    } finally {
      setRetrying(false);
    }
  }

  function reset() {
    setJob(null);
    setFiles([]);
    if (fileInput.current) fileInput.current.value = '';
  }

  if (job) {
    const busy = job.status === 'PENDING' || job.status === 'RUNNING';
    const hasRetryable = job.items.some((i) => i.status === 'FAILED' && i.retryable);
    return (
      <section style={styles.card}>
        <header style={styles.head}>
          <div>
            <b style={{ fontSize: 14 }}>가져오기 결과</b>
            <div style={styles.sub}>
              전체 {job.fileCount}건 · 성공 {job.successCount} · 실패 {job.failCount}
            </div>
          </div>
          <Badge tone={busy ? 'accent' : job.failCount > 0 ? 'warn' : 'ok'} >{JOB_STATUS_LABEL[job.status]}</Badge>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {hasRetryable && !busy && (
              <Button onClick={retry} disabled={retrying}>{retrying ? '재시도 중…' : '실패 건 재시도'}</Button>
            )}
            <Button variant="primary" onClick={() => { reset(); onDone(); }}>문서 목록으로</Button>
          </div>
        </header>

        {error && <div style={styles.error}>{error}</div>}

        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>파일</th>
              <th style={styles.th}>추출된 제목</th>
              <th style={styles.th}>작성자</th>
              <th style={styles.th}>작성일</th>
              <th style={styles.th}>결과</th>
              <th style={styles.th}>사유</th>
            </tr>
          </thead>
          <tbody>
            {job.items.map((item: ImportItem) => (
              <tr key={item.id}>
                <td style={styles.td}>{item.filename}</td>
                <td style={styles.td}>{item.extractedTitle ?? '—'}</td>
                <td style={styles.td}>{item.extractedAuthorName ?? '—'}</td>
                <td style={styles.td}>{item.extractedAuthoredAt ?? '—'}</td>
                <td style={styles.td}>
                  <Badge tone={item.status === 'SUCCESS' ? 'ok' : 'err'}>
                    {item.status === 'SUCCESS' ? '성공' : '실패'}
                  </Badge>
                </td>
                <td style={styles.td}>
                  {item.failureReason ?? '—'}
                  {item.status === 'FAILED' && !item.retryable && (
                    <span style={styles.noRetry}> (재시도 불가)</span>
                  )}
                </td>
              </tr>
            ))}
            {job.items.length === 0 && (
              <tr><td colSpan={6} style={styles.empty}>{busy ? '처리 중입니다…' : '처리된 파일이 없습니다.'}</td></tr>
            )}
          </tbody>
        </table>
      </section>
    );
  }

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <div>
          <b style={{ fontSize: 14 }}>문서 일괄 가져오기</b>
          <div style={styles.sub}>md · txt · PDF · docx · pptx · xlsx 여러 건을 한 번에 등록한다. 일부가 실패해도 나머지는 등록된다.</div>
        </div>
      </header>

      <div style={{ padding: 20 }}>
        {error && <div style={styles.error}>{error}</div>}

        <Field label="파일" required error={fieldErrors.files}>
          <input ref={fileInput} type="file" multiple accept=".md,.txt,.pdf,.docx,.pptx,.xlsx"
                 onChange={(e) => setFiles(Array.from(e.target.files ?? []))} style={{ border: 'none', padding: 0 }} />
          {files.length > 0 && <div style={styles.hint}>{files.length}개 파일 선택됨</div>}
        </Field>

        <div style={styles.grid2}>
          <Field label="기본 문서 유형" required>
            <select value={documentType} onChange={(e) => setDocumentType(e.target.value as DocumentType)} style={styles.select}>
              {TYPES.map((t) => <option key={t} value={t}>{DOCUMENT_TYPE_LABEL[t]}</option>)}
            </select>
            <div style={styles.hint}>파일에서 유형을 알 수 없으므로 전체 파일에 공통 적용한다.</div>
          </Field>
          <Field label="팀">
            <select value={teamId} onChange={(e) => setTeamId(e.target.value)} style={styles.select}>
              <option value="">선택 안 함</option>
              {user.teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
        </div>

        <Button variant="primary" onClick={submit} disabled={submitting}>
          {submitting ? '업로드 중…' : '가져오기 실행'}
        </Button>
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'center', gap: 12, padding: '14px 18px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  sub: { fontSize: 12, color: 'var(--ink-3)', marginTop: 2 },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' },
  select: { width: '100%', padding: '10px 12px', border: '1px solid var(--rule)', borderRadius: 6, background: 'var(--paper)', font: 'inherit' },
  hint: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 4 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '10px 14px', borderBottom: '1px solid var(--rule-soft)' },
  noRetry: { fontSize: 11, color: 'var(--ink-3)' },
  empty: { padding: 28, textAlign: 'center', color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
};
