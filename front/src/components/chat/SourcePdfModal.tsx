import { useEffect, useRef, useState } from 'react';
import * as pdfjsLib from 'pdfjs-dist';
import { api, HttpError } from '../../api/client';
import { DOCUMENT_TYPE_LABEL, type AnswerSource, type DocumentDetail } from '../../api/types';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';

// PDF.js worker 설정 (CDN fallback)
if (typeof window !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.js`;
}

interface Props {
  source: AnswerSource;
  onClose: () => void;
  onOpenDocument?: (docId: number) => void;
}

export function SourcePdfModal({ source, onClose, onOpenDocument }: Props) {
  const [docDetail, setDocDetail] = useState<DocumentDetail | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // PDF 렌더링 상태. 근거 청크가 시작하는 페이지부터 연다
  const [numPages, setNumPages] = useState<number>(1);
  const [pageNumber, setPageNumber] = useState<number>(source.pageStart ?? 1);
  const [scale, setScale] = useState<number>(1.2);
  const [isPdf, setIsPdf] = useState<boolean>(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderTaskRef = useRef<any>(null);

  // 문서 상세 및 다운로드 URL 로드
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);

    async function load() {
      try {
        const detail = await api.document(source.documentId);
        if (!active) return;
        setDocDetail(detail);

        if (detail.file) {
          const isPdfFile = detail.file.mimeType.includes('pdf') ||
            detail.file.originalFilename.toLowerCase().endsWith('.pdf');
          setIsPdf(isPdfFile);

          try {
            const issued = await api.downloadUrl(detail.file.id);
            if (active) setDownloadUrl(issued.downloadUrl);
          } catch {
            // 다운로드 URL 발급 실패 시 텍스트 모드로 폴백
          }
        }
      } catch (e) {
        if (active) setError(e instanceof HttpError ? e.body.message : '문서 정보를 불러오지 못했습니다.');
      } finally {
        if (active) setLoading(false);
      }
    }

    void load();
    return () => { active = false; };
  }, [source.documentId]);

  // PDF 캔버스 렌더링
  useEffect(() => {
    if (!isPdf || !downloadUrl || !canvasRef.current) return;

    let cancelled = false;

    async function renderPage() {
      try {
        if (renderTaskRef.current) {
          renderTaskRef.current.cancel();
        }

        const loadingTask = pdfjsLib.getDocument({ url: downloadUrl! });
        const pdf = await loadingTask.promise;
        if (cancelled) return;
        setNumPages(pdf.numPages);

        const page = await pdf.getPage(pageNumber);
        if (cancelled) return;

        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current;
        if (!canvas) return;

        const context = canvas.getContext('2d');
        if (!context) return;

        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
          canvasContext: context,
          viewport: viewport,
        };

        const task = page.render(renderContext);
        renderTaskRef.current = task;
        await task.promise;
      } catch (err: any) {
        if (err?.name !== 'RenderingCancelledException') {
          console.warn('PDF 렌더링 오류, 텍스트 뷰어로 폴백', err);
          setIsPdf(false);
        }
      }
    }

    void renderPage();
    return () => {
      cancelled = true;
      if (renderTaskRef.current) renderTaskRef.current.cancel();
    };
  }, [isPdf, downloadUrl, pageNumber, scale]);

  // ESC 키 닫기
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div style={styles.backdrop} onClick={onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        {/* 모달 상단 헤더 */}
        <header style={styles.header}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <Badge tone="accent">{DOCUMENT_TYPE_LABEL[source.documentType]}</Badge>
              {source.chunkTag && <Badge>{source.chunkTag}</Badge>}
              <span style={{ fontSize: 11, color: '#64748b' }}>
                청크 #{source.chunkNo} · v{source.documentVersion}
              </span>
            </div>
            <h2 style={styles.title}>{source.documentTitle}</h2>
            {source.sectionPath && (
              <div style={styles.sectionPath}>
                위치: <b>{source.sectionPath}</b>
              </div>
            )}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {docDetail && onOpenDocument && (
              <Button size="sm" onClick={() => onOpenDocument(source.documentId)}>
                문서 상세
              </Button>
            )}
            <button onClick={onClose} style={styles.closeBtn}>✕</button>
          </div>
        </header>

        {/* 툴바 (PDF 모드 시) */}
        {isPdf && downloadUrl && (
          <div style={styles.toolbar}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Button size="sm" onClick={() => setPageNumber((p) => Math.max(1, p - 1))} disabled={pageNumber <= 1}>
                ‹ 이전
              </Button>
              <span style={{ fontSize: 12, color: '#334155', fontWeight: 600 }}>
                {pageNumber} / {numPages}
              </span>
              <Button size="sm" onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))} disabled={pageNumber >= numPages}>
                다음 ›
              </Button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <Button size="sm" onClick={() => setScale((s) => Math.max(0.8, s - 0.2))}>축소 -</Button>
              <span style={{ fontSize: 12, color: '#64748b' }}>{Math.round(scale * 100)}%</span>
              <Button size="sm" onClick={() => setScale((s) => Math.min(2.0, s + 0.2))}>확대 +</Button>
              <Button size="sm" variant="primary" onClick={() => window.open(downloadUrl, '_blank')}>
                원본 다운로드
              </Button>
            </div>
          </div>
        )}

        {/* 본문 영역 */}
        <div style={styles.contentArea}>
          {loading && <div style={styles.notice}>문서 원문 불러오는 중…</div>}
          {error && <div style={styles.errorNotice}>{error}</div>}

          {!loading && isPdf && downloadUrl && (
            <div style={styles.pdfContainer}>
              <div style={styles.bboxBadge}>
                🎯 검색 근거 단락 (청크 #{source.chunkNo}
                {source.pageStart && (
                  source.pageEnd && source.pageEnd !== source.pageStart
                    ? ` · p.${source.pageStart}-${source.pageEnd}`
                    : ` · p.${source.pageStart}`
                )})
              </div>
              <div style={styles.canvasWrapper}>
                <canvas ref={canvasRef} style={styles.canvas} />
                {/* 현재 쪽에 걸리는 바운딩 박스만 하이라이트한다 */}
                {(source.bboxes ?? [])
                  .filter((box) => box.page === pageNumber)
                  .map((box, i) => (
                    <div
                      key={i}
                      style={{
                        ...styles.highlightOverlay,
                        left: `${box.x * 100}%`,
                        top: `${box.y * 100}%`,
                        width: `${box.w * 100}%`,
                        height: `${box.h * 100}%`,
                      }}
                    />
                  ))}
              </div>
            </div>
          )}

          {!loading && (!isPdf || !downloadUrl) && (
            <div style={styles.textContainer}>
              <div style={styles.matchedBanner}>
                <b>💡 인용 근거 위치</b>: {source.sectionPath || '본문 조항'}
                {source.similarity && (
                  <span style={{ marginLeft: 8, fontSize: 11, color: '#15803d' }}>
                    (유사도: {(source.similarity * 100).toFixed(1)}%)
                  </span>
                )}
              </div>

              {docDetail?.content ? (
                <div style={styles.docText}>
                  {docDetail.content.split('\n\n').map((para, i) => {
                    // 키워드나 청크 관련 문단 하이라이트
                    const isMatched = source.sectionPath && para.includes(source.sectionPath);
                    return (
                      <p
                        key={i}
                        style={{
                          margin: '0 0 12px 0',
                          padding: isMatched ? '8px 12px' : 0,
                          borderRadius: isMatched ? 6 : 0,
                          background: isMatched ? '#fef08a' : 'transparent',
                          borderLeft: isMatched ? '4px solid #eab308' : 'none',
                          lineHeight: 1.6,
                        }}
                      >
                        {para}
                      </p>
                    );
                  })}
                </div>
              ) : (
                <div style={styles.notice}>본문 내용이 없습니다.</div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(15, 23, 42, 0.65)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 1000,
    backdropFilter: 'blur(3px)',
  },
  modal: {
    width: '90%',
    maxWidth: 900,
    height: '86vh',
    backgroundColor: '#ffffff',
    borderRadius: 12,
    boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    padding: '16px 20px',
    borderBottom: '1px solid #e2e8f0',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    backgroundColor: '#ffffff',
  },
  title: {
    fontSize: 16,
    fontWeight: 700,
    color: '#0f172a',
    margin: '4px 0 2px 0',
  },
  sectionPath: {
    fontSize: 12,
    color: '#64748b',
  },
  closeBtn: {
    border: 'none',
    background: 'transparent',
    fontSize: 18,
    cursor: 'pointer',
    color: '#94a3b8',
    padding: '2px 8px',
  },
  toolbar: {
    padding: '8px 20px',
    backgroundColor: '#f8fafc',
    borderBottom: '1px solid #e2e8f0',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  contentArea: {
    flex: 1,
    overflowY: 'auto',
    backgroundColor: '#f1f5f9',
    display: 'flex',
    justifyContent: 'center',
    // alignItems 기본값(stretch)이 pdfContainer를 보이는 뷰포트 높이로 짓눌러, 그 안의
    // canvasWrapper(overflow:hidden)가 캔버스 실제 높이보다 작게 찌그러지는 버그가 있었다.
    // 그 상태에서 하이라이트 박스의 top:% 는 찌그러진 wrapper 기준으로 계산돼 실제보다 훨씬 위에 찍혔다.
    alignItems: 'flex-start',
    padding: 20,
  },
  pdfContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 12,
  },
  bboxBadge: {
    background: '#fef08a',
    color: '#854d0e',
    padding: '6px 14px',
    borderRadius: 20,
    fontSize: 12,
    fontWeight: 700,
    border: '1px solid #facc15',
    boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
  },
  canvasWrapper: {
    position: 'relative',
    boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06)',
    borderRadius: 4,
    overflow: 'hidden',
    backgroundColor: '#ffffff',
    // overflow:hidden 은 flex 자동 최소 높이 보호를 없앤다. flexShrink:0 으로 캔버스 실제
    // 크기 밑으로 찌그러지지 않게 명시적으로 막는다(하이라이트 박스 좌표 기준이 되는 박스라
    // 캔버스와 항상 같은 크기여야 한다).
    flexShrink: 0,
  },
  canvas: {
    display: 'block',
  },
  highlightOverlay: {
    position: 'absolute',
    // 일반 PDF 형광펜처럼 은은한 음영만
    backgroundColor: 'rgba(250, 204, 21, 0.18)',
    mixBlendMode: 'multiply',
    pointerEvents: 'none',
  },
  textContainer: {
    width: '100%',
    maxWidth: 780,
    backgroundColor: '#ffffff',
    borderRadius: 8,
    padding: 24,
    boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
  },
  matchedBanner: {
    background: '#fef9c3',
    borderLeft: '4px solid #ca8a04',
    padding: '10px 14px',
    borderRadius: 4,
    fontSize: 13,
    color: '#854d0e',
    marginBottom: 20,
  },
  docText: {
    fontSize: 14,
    color: '#334155',
  },
  notice: {
    textAlign: 'center',
    color: '#64748b',
    padding: 40,
    fontSize: 14,
  },
  errorNotice: {
    textAlign: 'center',
    color: '#ef4444',
    padding: 40,
    fontSize: 14,
  },
};
