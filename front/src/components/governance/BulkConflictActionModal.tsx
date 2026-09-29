import { useState } from 'react';
import { api, HttpError } from '../../api/client';
import type { ConflictItem } from '../../api/types';
import { Button } from '../ui/Button';

interface Props {
  conflicts: ConflictItem[];
  onClose: () => void;
  /** 성공한 항목만 넘어온다 — 실패한 항목은 목록에서 원래 상태 그대로 남는다. */
  onSuccess: (updated: ConflictItem[]) => void;
}

/**
 * 상충 일괄 처리. 새 벌크 API를 만들지 않고 건별 PATCH
 * (`api.resolveConflict`)를 병렬로 호출한다 — 서버 쪽 권한 검사·감사 로그가
 * 건마다 남는 기존 동작을 그대로 유지하면서, 일부만 실패해도 나머지는 반영된다.
 */
export function BulkConflictActionModal({ conflicts, onClose, onSuccess }: Props) {
  const [status, setStatus] = useState<'RESOLVED' | 'DISMISSED'>('RESOLVED');
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ id: number; message: string }[]>([]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!note.trim()) {
      setError('조치 사유 및 근거를 구체적으로 입력해주세요.');
      return;
    }

    setSubmitting(true);
    setError(null);
    setFailures([]);

    const results = await Promise.allSettled(
      conflicts.map((c) => api.resolveConflict(c.id, { status, resolutionNote: note.trim() })),
    );

    const succeeded: ConflictItem[] = [];
    const failed: { id: number; message: string }[] = [];
    results.forEach((result, idx) => {
      if (result.status === 'fulfilled') {
        succeeded.push(result.value);
      } else {
        const reason = result.reason;
        const message = reason instanceof HttpError ? reason.body.message : '처리 실패';
        failed.push({ id: conflicts[idx].id, message });
      }
    });

    setSubmitting(false);
    if (succeeded.length > 0) onSuccess(succeeded);
    if (failed.length > 0) {
      setFailures(failed);
    } else {
      onClose();
    }
  }

  return (
    <div style={styles.backdrop} onClick={submitting ? undefined : onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <header style={styles.header}>
          <div>
            <span style={styles.tag}>정책 거버넌스 일괄 조치</span>
            <h3 style={styles.title}>선택한 상충 {conflicts.length}건 일괄 처리</h3>
          </div>
          <button style={styles.closeBtn} onClick={onClose} disabled={submitting}>✕</button>
        </header>

        <form onSubmit={handleSubmit} style={styles.body}>
          <div style={styles.summaryBox}>
            <span style={styles.summaryLabel}>선택된 상충 건 ({conflicts.length})</span>
            <ul style={styles.list}>
              {conflicts.map((c) => (
                <li key={c.id} style={styles.listItem}>
                  #{c.id} — {c.sourceDocTitle} ↔ {c.targetDocTitle}
                  {failures.some((f) => f.id === c.id) && (
                    <span style={styles.failTag}>
                      실패: {failures.find((f) => f.id === c.id)?.message}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>조치 상태 결정 <span style={{ color: 'var(--err)' }}>*</span></label>
            <div style={styles.radioGroup}>
              <label style={radioStyle(status === 'RESOLVED')}>
                <input
                  type="radio" name="status" value="RESOLVED"
                  checked={status === 'RESOLVED'} onChange={() => setStatus('RESOLVED')}
                />
                <strong style={{ color: 'var(--ok)' }}>✅ 공식 해결 (RESOLVED)</strong>
              </label>
              <label style={radioStyle(status === 'DISMISSED')}>
                <input
                  type="radio" name="status" value="DISMISSED"
                  checked={status === 'DISMISSED'} onChange={() => setStatus('DISMISSED')}
                />
                <strong style={{ color: 'var(--ink-2)' }}>⚡ 예외 허용 / 무시 (DISMISSED)</strong>
              </label>
            </div>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>
              조치 사유 및 근거 의결 사항 <span style={{ color: 'var(--err)' }}>*</span>
            </label>
            <textarea
              style={styles.textarea}
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="선택한 모든 건에 같은 사유가 기록됩니다."
            />
            <span style={styles.hint}>
              * 선택한 {conflicts.length}건 모두에 같은 사유가 기록되며, 건별로 감사 로그가 남습니다.
            </span>
          </div>

          {error && <div style={styles.errorBox}>{error}</div>}
          {failures.length > 0 && (
            <div style={styles.errorBox}>
              {failures.length}건 처리 실패, {conflicts.length - failures.length}건은 반영됨. 실패 건은 위 목록에 표시됩니다.
            </div>
          )}

          <footer style={styles.footer}>
            <Button type="button" variant="default" onClick={onClose} disabled={submitting}>
              {failures.length > 0 ? '닫기' : '취소'}
            </Button>
            <Button type="submit" variant="primary" disabled={submitting}>
              {submitting ? '일괄 처리 중...' : `${conflicts.length}건 일괄 조치`}
            </Button>
          </footer>
        </form>
      </div>
    </div>
  );
}

function radioStyle(active: boolean): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '10px 14px',
    borderRadius: 8,
    border: `1.5px solid ${active ? 'var(--accent)' : 'var(--rule)'}`,
    background: active ? 'var(--tint)' : 'var(--paper)',
    cursor: 'pointer',
  };
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(0, 0, 0, 0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1100, padding: 20,
  },
  modal: {
    background: 'var(--paper)', borderRadius: 12,
    boxShadow: '0 20px 40px rgba(0, 0, 0, 0.2)',
    width: '100%', maxWidth: 640, border: '1px solid var(--rule)',
    overflow: 'hidden', display: 'flex', flexDirection: 'column', maxHeight: '85vh',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
    padding: '18px 24px', borderBottom: '1px solid var(--rule)', background: 'var(--tint)',
  },
  tag: {
    fontSize: 11, fontWeight: 700, color: 'var(--accent)',
    textTransform: 'uppercase', letterSpacing: '0.05em',
  },
  title: { margin: '4px 0 0', fontSize: 16, fontWeight: 800, color: 'var(--ink)' },
  closeBtn: {
    background: 'transparent', border: 'none', fontSize: 18, cursor: 'pointer',
    color: 'var(--ink-3)', padding: '4px 8px',
  },
  body: { padding: 24, display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto' },
  summaryBox: {
    padding: '14px 16px', borderRadius: 8, background: 'var(--tint)',
    border: '1px solid var(--rule)', display: 'flex', flexDirection: 'column', gap: 8,
  },
  summaryLabel: { fontSize: 12, fontWeight: 700, color: 'var(--ink-3)' },
  list: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 },
  listItem: { fontSize: 12.5, color: 'var(--ink)', lineHeight: 1.5 },
  failTag: { color: 'var(--err)', fontWeight: 700, marginLeft: 8 },
  field: { display: 'flex', flexDirection: 'column', gap: 8 },
  label: { fontSize: 13, fontWeight: 700, color: 'var(--ink)' },
  radioGroup: { display: 'grid', gridTemplateColumns: '1fr', gap: 8 },
  textarea: {
    width: '100%', padding: '10px 12px', borderRadius: 6, border: '1px solid var(--rule)',
    background: 'var(--paper)', color: 'var(--ink)', fontSize: 13, lineHeight: 1.5,
    boxSizing: 'border-box', fontFamily: 'inherit', resize: 'vertical',
  },
  hint: { fontSize: 11, color: 'var(--ink-3)' },
  errorBox: {
    padding: '10px 14px', borderRadius: 6, background: 'var(--err-bg)', color: 'var(--err)',
    border: '1px solid #f5c9c9', fontSize: 12, fontWeight: 600,
  },
  footer: {
    display: 'flex', justifyContent: 'flex-end', gap: 10,
    marginTop: 6, paddingTop: 16, borderTop: '1px solid var(--rule)',
  },
};
