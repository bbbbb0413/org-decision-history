import { Suspense, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  DECISION_STATUS_LABEL, DOCUMENT_TYPE_LABEL,
  type DocumentDetail, type DocumentVersionInfo, type GraphData,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { DecisionGraphViewLazy } from '../chat/lazy';
import { VersionCompareView } from './VersionCompareView';

interface Props {
  documentId: number;
  onBack: () => void;
  onEdit: (id: number) => void;
  onDeleted: () => void;
}

/** 문서 상세. 메타데이터, 원본 파일, 본문, 버전 이력을 보여준다. */
export function DocumentDetailView({ documentId, onBack, onEdit, onDeleted }: Props) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [versions, setVersions] = useState<DocumentVersionInfo[]>([]);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    setError(null);
    api.document(documentId).then(setDoc).catch((e) =>
      setError(e instanceof HttpError ? e.body.message : '문서를 불러오지 못했습니다.'));
    api.versions(documentId).then(setVersions).catch(() => setVersions([]));
    api.documentGraph(documentId).then(setGraphData).catch(() => setGraphData(null));
  }, [documentId]);

  /** 누른 시점에 경로를 발급한다. 발급 직전 권한을 재확인하고 유효 시간은 60초다. */
  async function download(fileId: number) {
    setNotice(null);
    try {
      const issued = await api.downloadUrl(fileId);
      window.open(issued.downloadUrl, '_blank', 'noopener');
      const seconds = Math.max(0, Math.round((Date.parse(issued.expiresAt) - Date.now()) / 1000));
      setNotice(`다운로드 경로를 발급했습니다. ${seconds}초 후 만료됩니다.`);
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '다운로드 경로를 발급하지 못했습니다.');
    }
  }

  async function remove() {
    if (!window.confirm('이 문서를 삭제할까요? 검색과 답변 결과에서 제외됩니다.')) return;
    try {
      await api.deleteDocument(documentId);
      onDeleted();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '삭제하지 못했습니다.');
    }
  }

  if (error && !doc) return <div style={styles.error}>{error}</div>;
  if (!doc) return <div style={styles.loading}>불러오는 중…</div>;

  return (
    <article style={styles.card}>
      <header style={styles.head}>
        <Button variant="ghost" onClick={onBack}>‹ 목록</Button>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Button onClick={() => onEdit(documentId)}>편집</Button>
          <Button variant="danger" onClick={remove}>삭제</Button>
        </div>
      </header>

      <div style={styles.body}>
        <h1 style={styles.title}>{doc.title}</h1>
        <div style={styles.badges}>
          <Badge tone="accent">{DOCUMENT_TYPE_LABEL[doc.documentType]}</Badge>
          <Badge tone={doc.decisionStatus === 'CURRENT' ? 'ok' : 'neutral'}>
            {DECISION_STATUS_LABEL[doc.decisionStatus]}
          </Badge>
          <Badge>v{doc.currentVersion}</Badge>
        </div>

        <section style={styles.section}>
          <div style={styles.sectionLabel}>메타데이터</div>
          <dl style={styles.meta}>
            <Meta k="작성자" v={doc.authorName} />
            <Meta k="작성일" v={doc.authoredAt} />
            <Meta k="갱신일" v={new Date(doc.updatedAt).toLocaleString('ko-KR')} />
            <Meta k="관련 프로젝트" v={doc.projectName ?? '—'} />
          </dl>
        </section>

        {doc.file && (
          <section style={styles.section}>
            <div style={styles.sectionLabel}>원본 파일</div>
            <div style={styles.fileRow}>
              <div>
                <b>{doc.file.originalFilename}</b>
                <div style={styles.fileMeta}>
                  {doc.file.mimeType} · {(doc.file.sizeBytes / 1024).toFixed(0)} KB ·
                  {' '}{new Date(doc.file.uploadedAt).toLocaleDateString('ko-KR')} 업로드
                </div>
              </div>
              <Button variant="primary" onClick={() => download(doc.file!.id)} style={{ marginLeft: 'auto' }}>
                원본 받기
              </Button>
            </div>
            {notice && <div style={styles.notice}>{notice}</div>}
          </section>
        )}

        <section style={styles.section}>
          <div style={styles.sectionLabel}>본문</div>
          <pre style={styles.content}>{doc.content}</pre>
        </section>

        {graphData && graphData.nodes.length > 0 && (
          <section style={styles.section}>
            <div style={styles.sectionLabel}>의사결정 계보 및 상충 관계망 (DAG)</div>
            <Suspense fallback={<div style={styles.lazyFallback}>관계망을 불러오는 중…</div>}>
              <DecisionGraphViewLazy
                graphData={graphData}
                title={`${doc.title} 관련 인과/상충 관계망 (${graphData.nodes.length}개 노드, ${graphData.edges.length}개 관계)`}
                height={380}
              />
            </Suspense>
          </section>
        )}

        {versions.length > 0 && (
          <section style={styles.section}>
            <div style={styles.versionHead}>
              <div style={styles.sectionLabel}>버전 이력</div>
              {versions.length > 1 && (
                <Button size="sm" onClick={() => setComparing((v) => !v)}>
                  {comparing ? '비교 닫기' : '버전 비교'}
                </Button>
              )}
            </div>

            {comparing && (
              <VersionCompareView
                documentId={documentId}
                versions={versions}
                onClose={() => setComparing(false)}
              />
            )}

            <table style={styles.table}>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version}>
                    <td style={{ ...styles.td, width: 60 }}><b>v{v.version}</b></td>
                    <td style={styles.td}>{new Date(v.createdAt).toLocaleDateString('ko-KR')}</td>
                    <td style={styles.td}>{v.changeNote ?? '—'}</td>
                    <td style={{ ...styles.td, textAlign: 'right' }}>
                      {v.fileId
                        ? <Button size="sm" onClick={() => download(v.fileId!)}>원본 받기</Button>
                        : <span style={{ color: 'var(--rule)', fontSize: 12 }}>원본 없음</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {error && <div style={styles.error}>{error}</div>}
      </div>
    </article>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt style={{ fontSize: 11, color: 'var(--ink-3)', fontWeight: 700, margin: 0 }}>{k}</dt>
      <dd style={{ margin: '2px 0 0', fontSize: 13, fontWeight: 600 }}>{v}</dd>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  // 지연 로딩되는 무거운 뷰(관계망·PDF 뷰어)를 받는 동안 자리를 지킨다
  lazyFallback: { padding: 16, textAlign: 'center', fontSize: 12.5, color: 'var(--ink-3)' },
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  body: { padding: 22 },
  title: { fontSize: 20, fontWeight: 800, margin: '0 0 8px', letterSpacing: '-.01em' },
  badges: { display: 'flex', gap: 6, marginBottom: 20 },
  section: { border: '1px dashed var(--rule)', borderRadius: 8, padding: 14, marginBottom: 14 },
  sectionLabel: { fontSize: 10.5, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.08em', marginBottom: 10 },
  versionHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 },
  meta: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, margin: 0 },
  fileRow: { display: 'flex', alignItems: 'center', gap: 12, fontSize: 13 },
  fileMeta: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 2 },
  notice: { marginTop: 10, fontSize: 12, color: 'var(--accent)', background: 'var(--accent-bg)', padding: '7px 10px', borderRadius: 5 },
  content: {
    whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.8,
    margin: 0, maxHeight: 320, overflowY: 'auto',
  },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  td: { padding: '8px 4px', borderBottom: '1px solid var(--rule-soft)' },
  error: { padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  loading: { padding: 40, textAlign: 'center', color: 'var(--ink-3)' },
};
