import { useEffect, useMemo, useState } from 'react';
import { api, HttpError } from '../../api/client';
import type { DocumentVersionDetail, DocumentVersionInfo } from '../../api/types';
import { collapseUnchanged, diffLines, MAX_DIFF_LINES } from '../../lib/diff';
import { Button } from '../ui/Button';

interface Props {
  documentId: number;
  versions: DocumentVersionInfo[];
  onClose: () => void;
}

/**
 * 두 버전의 본문을 줄 단위로 비교한다.
 * 그동안 버전 이력은 "언제 누가 고쳤다"만 보여줬고 정작 무엇이 바뀌었는지는 볼 수 없었다.
 */
export function VersionCompareView({ documentId, versions, onClose }: Props) {
  // 기본값은 "직전 버전 → 최신 버전". 가장 자주 궁금해하는 비교다.
  const sorted = useMemo(() => [...versions].sort((a, b) => b.version - a.version), [versions]);
  const [baseVersion, setBaseVersion] = useState(sorted[Math.min(1, sorted.length - 1)]?.version ?? 1);
  const [targetVersion, setTargetVersion] = useState(sorted[0]?.version ?? 1);

  const [base, setBase] = useState<DocumentVersionDetail | null>(null);
  const [target, setTarget] = useState<DocumentVersionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.version(documentId, baseVersion), api.version(documentId, targetVersion)])
      .then(([b, t]) => {
        if (cancelled) return;
        setBase(b);
        setTarget(t);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof HttpError ? err.body.message : '버전 본문을 불러오지 못했습니다.');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [documentId, baseVersion, targetVersion]);

  const diff = useMemo(
    () => (base && target ? diffLines(base.content, target.content) : null),
    [base, target],
  );
  const displayRows = useMemo(
    () => (diff && !showAll ? collapseUnchanged(diff.rows) : diff?.rows ?? []),
    [diff, showAll],
  );

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <div style={styles.selectors}>
          <select value={baseVersion} style={styles.select}
                  onChange={(e) => setBaseVersion(Number(e.target.value))}>
            {sorted.map((v) => <option key={v.version} value={v.version}>v{v.version} (이전)</option>)}
          </select>
          <span style={styles.arrow}>→</span>
          <select value={targetVersion} style={styles.select}
                  onChange={(e) => setTargetVersion(Number(e.target.value))}>
            {sorted.map((v) => <option key={v.version} value={v.version}>v{v.version} (이후)</option>)}
          </select>
          {diff && !diff.tooLarge && (
            <span style={styles.counts}>
              <span style={styles.addedText}>+{diff.addedCount}</span>{' '}
              <span style={styles.removedText}>−{diff.removedCount}</span>
            </span>
          )}
        </div>
        <Button size="sm" onClick={onClose}>비교 닫기</Button>
      </header>

      {error && <div style={styles.error}>{error}</div>}
      {loading && <div style={styles.empty}>본문을 불러오는 중…</div>}

      {!loading && diff?.tooLarge && (
        <div style={styles.empty}>
          문서가 너무 길어 비교를 생략했습니다(한쪽이 {MAX_DIFF_LINES}줄을 넘습니다). 각 버전 본문은 아래에서 그대로 볼 수 있습니다.
        </div>
      )}
      {!loading && diff && !diff.tooLarge && diff.identical && (
        <div style={styles.empty}>
          두 버전의 본문이 같습니다{baseVersion === targetVersion ? '' : ' (제목·상태만 달라졌을 수 있습니다)'}.
        </div>
      )}

      {!loading && diff && !diff.tooLarge && !diff.identical && (
        <>
          <div style={styles.diffBox}>
            {displayRows.map((row, index) => (
              row.kind === 'gap' ? (
                <div key={`gap-${index}`} style={styles.gap}>⋯ 변경 없는 {row.hidden}줄</div>
              ) : (
                <div key={`${row.kind}-${index}`} style={rowStyle(row.kind)}>
                  <span style={styles.lineNo}>{row.oldLineNo ?? ''}</span>
                  <span style={styles.lineNo}>{row.newLineNo ?? ''}</span>
                  <span style={styles.marker}>{row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : ' '}</span>
                  <span style={styles.lineText}>{row.text || ' '}</span>
                </div>
              )
            ))}
          </div>
          <div style={styles.footer}>
            <Button size="sm" onClick={() => setShowAll((v) => !v)}>
              {showAll ? '변경된 부분만 보기' : '전체 본문 보기'}
            </Button>
          </div>
        </>
      )}

      {!loading && base && target && (
        <div style={styles.metaRow}>
          <span>v{base.version}: {base.changeNote ?? '변경 메모 없음'} · {new Date(base.createdAt).toLocaleString('ko-KR')}</span>
          <span>v{target.version}: {target.changeNote ?? '변경 메모 없음'} · {new Date(target.createdAt).toLocaleString('ko-KR')}</span>
        </div>
      )}
    </section>
  );
}

function rowStyle(kind: 'same' | 'added' | 'removed'): React.CSSProperties {
  const base: React.CSSProperties = {
    display: 'flex', gap: 8, padding: '1px 8px',
    fontFamily: 'var(--mono)', fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  };
  if (kind === 'added') return { ...base, background: 'rgba(22, 163, 74, 0.10)' };
  if (kind === 'removed') return { ...base, background: 'rgba(239, 68, 68, 0.10)' };
  return base;
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 8,
    marginTop: 12, overflow: 'hidden',
  },
  head: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
    flexWrap: 'wrap', padding: '10px 12px', borderBottom: '1px solid var(--rule)',
    background: 'var(--tint)',
  },
  selectors: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  select: {
    padding: '5px 8px', border: '1px solid var(--rule)', borderRadius: 6,
    background: 'var(--paper)', font: 'inherit', fontSize: 12.5,
  },
  arrow: { color: 'var(--ink-3)', fontSize: 13 },
  counts: { fontSize: 12.5, fontWeight: 700, marginLeft: 4 },
  addedText: { color: '#16a34a' },
  removedText: { color: 'var(--err)' },
  diffBox: { maxHeight: 460, overflowY: 'auto', padding: '6px 0' },
  gap: {
    padding: '4px 12px', fontSize: 11.5, color: 'var(--ink-3)',
    background: 'var(--tint)', borderTop: '1px solid var(--rule)', borderBottom: '1px solid var(--rule)',
  },
  lineNo: {
    width: 34, flexShrink: 0, textAlign: 'right', color: 'var(--ink-3)',
    userSelect: 'none', fontSize: 11,
  },
  marker: { width: 10, flexShrink: 0, color: 'var(--ink-3)' },
  lineText: { flex: 1 },
  footer: { padding: '8px 12px', borderTop: '1px solid var(--rule)' },
  metaRow: {
    display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
    padding: '8px 12px', borderTop: '1px solid var(--rule)',
    fontSize: 11.5, color: 'var(--ink-3)',
  },
  error: {
    margin: 12, padding: '9px 12px', borderRadius: 6,
    background: 'var(--err-bg)', color: 'var(--err)', fontSize: 12.5, fontWeight: 600,
  },
  empty: { padding: 24, textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 },
};
