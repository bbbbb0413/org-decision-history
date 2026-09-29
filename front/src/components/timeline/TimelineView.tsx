import { useState, type FormEvent } from 'react';
import { api, HttpError } from '../../api/client';
import {
  DECISION_STATUS_LABEL, DOCUMENT_TYPE_LABEL,
  type DecisionStatus, type TimelineResponse,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

interface Props {
  initialTopic?: string;
  onOpenDocument: (documentId: number) => void;
}

const STATUS_TONE: Record<DecisionStatus, 'ok' | 'warn' | 'err' | 'neutral'> = {
  CURRENT: 'ok', CHANGED: 'warn', DEPRECATED: 'err', UNDECIDED: 'neutral',
};

/**
 * 의사결정 타임라인.
 *
 * 왼쪽은 시간순 결정, 오른쪽은 상충 정보다.
 * 최신 문서를 정답으로 단정하지 않는다. 순서와 상태를 보여주고 판단은 사용자가 한다.
 */
export function TimelineView({ initialTopic = '', onOpenDocument }: Props) {
  const [topic, setTopic] = useState(initialTopic);
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(event?: FormEvent) {
    event?.preventDefault();
    if (topic.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      setData(await api.timeline(topic.trim()));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '타임라인을 불러오지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  async function download(fileId: number) {
    try {
      const issued = await api.downloadUrl(fileId);
      window.open(issued.downloadUrl, '_blank', 'noopener');
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '경로를 발급하지 못했습니다.');
    }
  }

  return (
    <section style={styles.card}>
      <form onSubmit={load} style={styles.head}>
        <input
          value={topic}
          placeholder="주제 · 프로젝트 · 기술 이름 (예: 게이트웨이)"
          onChange={(e) => setTopic(e.target.value)}
          style={{ flex: 1 }}
        />
        <Button variant="primary" type="submit" disabled={busy || topic.trim().length < 2}>
          {busy ? '분석 중…' : '타임라인 보기'}
        </Button>
      </form>

      {error && <div style={styles.error}>{error}</div>}

      {!data && !error && (
        <div style={styles.blank}>
          주제를 입력하면 관련 문서를 시간순으로 잇고 상충하는 결정을 따로 보여줍니다.
        </div>
      )}

      {data && (
        <div style={styles.body}>
          <div style={styles.timeline}>
            <div style={styles.label}>결정 타임라인 · 작성일 오름차순 {data.events.length}건</div>
            {data.events.length === 0 && (
              <div style={styles.blank}>권한 범위 안에서 관련 문서를 찾지 못했습니다.</div>
            )}
            {data.events.map((e) => (
              <article key={e.documentId} style={styles.event}>
                <div style={styles.date}>{e.occurredAt ?? '작성일 미상'}</div>
                <div style={{ flex: 1 }}>
                  <div style={styles.eventHead}>
                    <b style={styles.eventTitle}>{e.documentTitle}</b>
                    <Badge tone={STATUS_TONE[e.decisionStatus]}>
                      {DECISION_STATUS_LABEL[e.decisionStatus]}
                    </Badge>
                  </div>
                  <div style={styles.eventMeta}>
                    {DOCUMENT_TYPE_LABEL[e.documentType]}
                    {e.projectName && ` · ${e.projectName}`}
                  </div>
                  {e.decisionExcerpt && <div style={styles.excerpt}>{e.decisionExcerpt}</div>}
                  <div style={styles.actions}>
                    <Button size="sm" onClick={() => onOpenDocument(e.documentId)}>원문</Button>
                    {e.fileId && <Button size="sm" onClick={() => download(e.fileId!)}>원본 받기</Button>}
                  </div>
                </div>
              </article>
            ))}
          </div>

          <aside style={styles.conflicts}>
            <div style={styles.label}>상충 정보 {data.conflicts.length}건</div>
            {data.conflicts.length === 0 ? (
              <div style={styles.blankSmall}>상충하는 결정을 찾지 못했습니다.</div>
            ) : (
              data.conflicts.map((c, i) => (
                <div key={i} style={styles.conflict}>
                  <div style={styles.conflictPair}>
                    <span>{c.documentATitle}</span>
                    <span style={styles.conflictDate}>{c.documentAAuthoredAt}</span>
                  </div>
                  <div style={styles.conflictPair}>
                    <span>{c.documentBTitle}</span>
                    <span style={styles.conflictDate}>{c.documentBAuthoredAt}</span>
                  </div>
                  <div style={styles.difference}>{c.difference}</div>
                </div>
              ))
            )}
            <p style={styles.note}>
              최신 문서를 정답으로 단정하지 않습니다. 어느 결정이 유효한지는 출처를 확인해 판단하세요.
            </p>
          </aside>
        </div>
      )}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', gap: 10, padding: 16, borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  body: { display: 'grid', gridTemplateColumns: '1fr 320px', alignItems: 'start' },
  timeline: { padding: 18, borderRight: '1px solid var(--rule)' },
  label: { fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.06em', marginBottom: 12 },
  event: { display: 'flex', gap: 14, paddingBottom: 16, marginBottom: 16, borderBottom: '1px solid var(--rule-soft)' },
  date: { width: 82, flexShrink: 0, fontSize: 11.5, color: 'var(--ink-3)', fontVariantNumeric: 'tabular-nums', paddingTop: 2 },
  eventHead: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' },
  eventTitle: { fontSize: 13.5 },
  eventMeta: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 3 },
  excerpt: { fontSize: 12.5, lineHeight: 1.7, marginTop: 8, padding: '8px 10px', background: 'var(--tint)', borderRadius: 5, whiteSpace: 'pre-wrap' },
  actions: { display: 'flex', gap: 6, marginTop: 8 },
  conflicts: { padding: 18 },
  conflict: {
    border: '1px solid #f0d8bf', background: 'var(--warn-bg)', borderRadius: 6,
    padding: '10px 12px', marginBottom: 10,
  },
  conflictPair: { display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12, fontWeight: 600, marginBottom: 2 },
  conflictDate: { color: 'var(--ink-3)', fontWeight: 400, fontSize: 11, whiteSpace: 'nowrap' },
  difference: { fontSize: 11.5, color: 'var(--warn)', marginTop: 6, lineHeight: 1.6 },
  note: { fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.7, marginTop: 14 },
  blank: { padding: '40px 20px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 13 },
  blankSmall: { fontSize: 12, color: 'var(--ink-3)' },
  error: { margin: 16, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
};
