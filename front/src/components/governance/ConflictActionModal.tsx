import { useState } from 'react';
import { api } from '../../api/client';
import type { ConflictItem, ConflictStatus } from '../../api/types';
import { Button } from '../ui/Button';

interface Props {
  conflict: ConflictItem;
  onClose: () => void;
  onSuccess: (updated: ConflictItem) => void;
}

/** 사람이 고를 수 있는 상태. OPEN 은 시작점, SUPERSEDED 는 개정 시 워커가 자동으로 넣는 값이라 제외한다. */
type ResolutionStatus = Extract<ConflictStatus, 'RESOLVED' | 'DISMISSED'>;

export function ConflictActionModal({ conflict, onClose, onSuccess }: Props) {
  const [status, setStatus] = useState<ResolutionStatus>(
    conflict.status === 'DISMISSED' ? 'DISMISSED' : 'RESOLVED'
  );
  const [note, setNote] = useState(conflict.resolutionNote ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!note.trim()) {
      setError('조치 사유 및 근거를 구체적으로 입력해주세요.');
      return;
    }
    // status 는 RESOLVED/DISMISSED 중 하나로만 초기화·변경되므로 별도 검증이 필요 없다.
    setSubmitting(true);
    setError(null);
    try {
      const updated = await api.resolveConflict(conflict.id, {
        status,
        resolutionNote: note.trim(),
      });
      onSuccess(updated);
    } catch (err: any) {
      setError(err?.message ?? '상충 처리 중 오류가 발생했습니다.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <header style={styles.header}>
          <div>
            <span style={styles.tag}>정책 거버넌스 조치</span>
            <h3 style={styles.title}>정책 상충 #{conflict.id} 거버넌스 심의 및 조치</h3>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>✕</button>
        </header>

        <form onSubmit={handleSubmit} style={styles.body}>
          {/* 상충 요약 정보 */}
          <div style={styles.summaryBox}>
            <div style={styles.pairRow}>
              <span style={styles.pairLabel}>신규 정책:</span>
              <strong style={styles.pairVal}>{conflict.sourceDocTitle}</strong>
            </div>
            <div style={styles.pairRow}>
              <span style={styles.pairLabel}>기존 정책:</span>
              <strong style={styles.pairVal}>{conflict.targetDocTitle}</strong>
            </div>
            <div style={styles.reasoningRow}>
              <span style={styles.reasoningLabel}>AI 감지 사유:</span>
              <span style={styles.reasoningText}>{conflict.reasoning}</span>
            </div>
          </div>

          {/* 조치 상태 선택 */}
          <div style={styles.field}>
            <label style={styles.label}>조치 상태 결정 <span style={{ color: 'var(--err)' }}>*</span></label>
            <div style={styles.radioGroup}>
              <label style={radioStyle(status === 'RESOLVED')}>
                <input
                  type="radio"
                  name="status"
                  value="RESOLVED"
                  checked={status === 'RESOLVED'}
                  onChange={() => setStatus('RESOLVED')}
                />
                <div>
                  <strong style={{ display: 'block', color: 'var(--ok)' }}>✅ 공식 해결 (RESOLVED)</strong>
                  <span style={styles.radioDesc}>
                    신규 정책이 기존 정책을 공식 대체/개정하거나, 위원회 심의를 통해 상충을 공식 해결함.
                  </span>
                </div>
              </label>

              <label style={radioStyle(status === 'DISMISSED')}>
                <input
                  type="radio"
                  name="status"
                  value="DISMISSED"
                  checked={status === 'DISMISSED'}
                  onChange={() => setStatus('DISMISSED')}
                />
                <div>
                  <strong style={{ display: 'block', color: 'var(--ink-2)' }}>⚡ 예외 허용 / 무시 (DISMISSED)</strong>
                  <span style={styles.radioDesc}>
                    특정 부서 한정 예외 규정이거나 일시적 유예 조치 등으로 인해 규정상 상충을 예외 인정함.
                  </span>
                </div>
              </label>
            </div>
          </div>

          {/* 조치 사유 입력 */}
          <div style={styles.field}>
            <label style={styles.label}>
              조치 사유 및 근거 의결 사항 <span style={{ color: 'var(--err)' }}>*</span>
            </label>
            <textarea
              style={styles.textarea}
              rows={4}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="예: 인사위원회 심의(2026-09호) 의결에 따라 2026 하반기 전사 근무제도 개정안이 기존 원격근무 가이드라인을 전면 대체하며, 구 정책은 효력이 상실됨."
            />
            <span style={styles.hint}>
              * 이 기록은 전사 감사 로그(Audit Log) 및 규정 변경 계보에 영구 보존됩니다.
            </span>
          </div>

          {error && <div style={styles.errorBox}>{error}</div>}

          <footer style={styles.footer}>
            <Button type="button" variant="default" onClick={onClose} disabled={submitting}>
              취소
            </Button>
            <Button type="submit" variant="primary" disabled={submitting}>
              {submitting ? '조치 저장 중...' : '조치 완료 및 저장'}
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
    alignItems: 'flex-start',
    gap: 10,
    padding: '12px 14px',
    borderRadius: 8,
    border: `1.5px solid ${active ? 'var(--accent)' : 'var(--rule)'}`,
    background: active ? 'var(--tint)' : 'var(--paper)',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  };
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(0, 0, 0, 0.55)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1100,
    padding: 20,
  },
  modal: {
    background: 'var(--paper)',
    borderRadius: 12,
    boxShadow: '0 20px 40px rgba(0, 0, 0, 0.2)',
    width: '100%',
    maxWidth: 640,
    border: '1px solid var(--rule)',
    overflow: 'hidden',
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    padding: '18px 24px',
    borderBottom: '1px solid var(--rule)',
    background: 'var(--tint)',
  },
  tag: {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--accent)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  title: {
    margin: '4px 0 0',
    fontSize: 16,
    fontWeight: 800,
    color: 'var(--ink)',
  },
  closeBtn: {
    background: 'transparent',
    border: 'none',
    fontSize: 18,
    cursor: 'pointer',
    color: 'var(--ink-3)',
    padding: '4px 8px',
  },
  body: {
    padding: 24,
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
  },
  summaryBox: {
    padding: '14px 16px',
    borderRadius: 8,
    background: 'var(--tint)',
    border: '1px solid var(--rule)',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  pairRow: {
    display: 'flex',
    gap: 8,
    fontSize: 13,
  },
  pairLabel: {
    color: 'var(--ink-3)',
    width: 65,
    flexShrink: 0,
  },
  pairVal: {
    color: 'var(--ink)',
  },
  reasoningRow: {
    marginTop: 4,
    paddingTop: 8,
    borderTop: '1px dashed var(--rule)',
    fontSize: 12,
    color: 'var(--ink-2)',
    lineHeight: 1.5,
  },
  reasoningLabel: {
    fontWeight: 700,
    marginRight: 6,
    color: 'var(--warn)',
  },
  reasoningText: {},
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  label: {
    fontSize: 13,
    fontWeight: 700,
    color: 'var(--ink)',
  },
  radioGroup: {
    display: 'grid',
    gridTemplateColumns: '1fr',
    gap: 8,
  },
  radioDesc: {
    display: 'block',
    fontSize: 12,
    color: 'var(--ink-3)',
    marginTop: 2,
    lineHeight: 1.4,
  },
  textarea: {
    width: '100%',
    padding: '10px 12px',
    borderRadius: 6,
    border: '1px solid var(--rule)',
    background: 'var(--paper)',
    color: 'var(--ink)',
    fontSize: 13,
    lineHeight: 1.5,
    boxSizing: 'border-box',
    fontFamily: 'inherit',
    resize: 'vertical',
  },
  hint: {
    fontSize: 11,
    color: 'var(--ink-3)',
  },
  errorBox: {
    padding: '10px 14px',
    borderRadius: 6,
    background: 'var(--err-bg)',
    color: 'var(--err)',
    border: '1px solid #f5c9c9',
    fontSize: 12,
    fontWeight: 600,
  },
  footer: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: 10,
    marginTop: 6,
    paddingTop: 16,
    borderTop: '1px solid var(--rule)',
  },
};
