import { Suspense, useState } from 'react';
import { api, HttpError } from '../../api/client';
import { DOCUMENT_TYPE_LABEL, type AnswerSource } from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { SourcePdfModalLazy } from './lazy';

interface Props {
  sources: AnswerSource[];
  messageId: number | null;
  onOpenDocument: (documentId: number) => void;
}

const TAG_LABEL: Record<string, string> = {
  DECISION: '결정사항', DISCUSSION: '논의', ACTION_ITEM: '액션아이템', BACKGROUND: '배경',
};

/** 답변 출처 패널. */
export function SourcePanel({ sources, messageId, onOpenDocument }: Props) {
  const [notice, setNotice] = useState<string | null>(null);
  const [rated, setRated] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [modalSource, setModalSource] = useState<AnswerSource | null>(null);

  /**
   * 누른 시점에 경로를 발급한다. 답변과 함께 미리 받지 않는 이유는
   * 유효 시간이 60초라 답을 읽는 동안 소진되기 때문이다.
   */
  async function download(fileId: number) {
    setNotice(null);
    try {
      const issued = await api.downloadUrl(fileId);
      window.open(issued.downloadUrl, '_blank', 'noopener');
      setNotice('다운로드 경로를 발급했습니다. 60초 후 만료됩니다.');
    } catch (e) {
      setNotice(e instanceof HttpError ? e.body.message : '경로를 발급하지 못했습니다.');
    }
  }

  async function rate(rating: 'HELPFUL' | 'NOT_HELPFUL') {
    if (!messageId) return;
    try {
      await api.putFeedback(messageId, rating, rating === 'NOT_HELPFUL' ? reason : undefined);
      setRated(rating);
    } catch {
      setNotice('평가를 저장하지 못했습니다.');
    }
  }

  return (
    <aside style={styles.panel}>
      <div style={styles.label}>출처 {sources.length}건</div>

      {sources.length === 0 && <div style={styles.empty}>표시할 출처가 없습니다.</div>}

      {sources.map((s) => (
        <article key={`${s.documentId}-${s.chunkNo}`} style={styles.card}>
          <b style={styles.title}>{s.documentTitle}</b>
          <div style={styles.meta}>
            {DOCUMENT_TYPE_LABEL[s.documentType]} · {s.authoredAt ?? '작성일 미상'} ·{' '}
            {s.similarity != null ? `유사도 ${s.similarity.toFixed(3)}` : '키워드 일치'}
          </div>
          {s.sectionPath && <div style={styles.section}>{s.sectionPath}</div>}
          <div style={styles.actions}>
            {s.chunkTag && <Badge tone="accent">{TAG_LABEL[s.chunkTag] ?? s.chunkTag}</Badge>}
            <Button size="sm" onClick={() => setModalSource(s)}>원문 위치</Button>
            {s.fileId && <Button size="sm" onClick={() => download(s.fileId!)}>원본 받기</Button>}
          </div>
        </article>
      ))}

      {modalSource && (
        <Suspense fallback={<div style={styles.lazyFallback}>원문 뷰어를 불러오는 중…</div>}>
          <SourcePdfModalLazy
            source={modalSource}
            onClose={() => setModalSource(null)}
            onOpenDocument={onOpenDocument}
          />
        </Suspense>
      )}

      {notice && <div style={styles.notice}>{notice}</div>}

      {messageId && sources.length > 0 && (
        <div style={styles.feedback}>
          <div style={styles.label}>이 답변이 도움이 되었나요</div>
          {rated ? (
            <div style={styles.rated}>{rated === 'HELPFUL' ? '유용함으로 평가했습니다.' : '의견을 반영하겠습니다.'}</div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 6 }}>
                <Button size="sm" onClick={() => rate('HELPFUL')}>유용함</Button>
                <Button size="sm" onClick={() => rate('NOT_HELPFUL')}>유용하지 않음</Button>
              </div>
              <input
                value={reason}
                placeholder="어떤 점이 잘못되었나요"
                onChange={(e) => setReason(e.target.value)}
                style={{ marginTop: 8, fontSize: 12 }}
              />
            </>
          )}
        </div>
      )}
    </aside>
  );
}

const styles: Record<string, React.CSSProperties> = {
  // 지연 로딩되는 무거운 뷰(관계망·PDF 뷰어)를 받는 동안 자리를 지킨다
  lazyFallback: { padding: 16, textAlign: 'center', fontSize: 12.5, color: 'var(--ink-3)' },
  panel: { width: 300, flexShrink: 0, borderLeft: '1px solid var(--rule)', padding: 16, overflowY: 'auto' },
  label: { fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.06em', marginBottom: 10 },
  card: { border: '1px solid var(--rule)', borderLeft: '2px solid var(--accent)', borderRadius: 6, padding: '10px 12px', marginBottom: 8 },
  title: { fontSize: 12.5, display: 'block', lineHeight: 1.45 },
  meta: { fontSize: 11, color: 'var(--ink-3)', marginTop: 2 },
  section: { fontSize: 11, color: 'var(--ink-2)', marginTop: 4 },
  actions: { display: 'flex', gap: 5, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' },
  notice: { fontSize: 11.5, color: 'var(--accent)', background: 'var(--accent-bg)', padding: '7px 9px', borderRadius: 5, marginTop: 8 },
  feedback: { marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--rule-soft)' },
  rated: { fontSize: 12, color: 'var(--ok)' },
  empty: { fontSize: 12, color: 'var(--ink-3)' },
};
