import { useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  METRIC_TARGETS,
  type FeedbackDetail, type FeedbackItem, type FeedbackRating, type Paged, type ProductMetrics,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const PERIODS = [
  { days: 7, label: '최근 7일' },
  { days: 30, label: '최근 30일' },
  { days: 90, label: '최근 90일' },
] as const;

/** 분모가 이보다 작으면 비율을 판정에 쓰지 않는다. 표본 3건의 67% 를 "미달"로 읽으면 잘못된 결론이 난다. */
const MIN_SAMPLE = 20;

const ANSWER_STATUS_LABEL: Record<string, string> = {
  OK: '정상',
  NO_EVIDENCE: '근거 없음',
  LOW_CONFIDENCE: '신뢰도 낮음',
  DEGRADED: '기능 저하',
  RATE_LIMITED: '한도 초과',
  UNKNOWN: '미기록',
};

/**
 * 제품 지표. 시스템 관리자만 본다.
 *
 * 운영 현황과 다른 화면이다 — 운영 현황은 "제품이 잘 도는가", 여기는 "제품이 쓸모 있는가".
 * 핵심 지표 8개 중 6개를 보여준다. 5번(색인 반영 지연)은 운영 현황에 있고,
 * 6번(온보딩 탐색 시간)은 원천 데이터가 아직 없다.
 */
export function ProductMetricsPanel() {
  const [days, setDays] = useState<number>(30);
  const [metrics, setMetrics] = useState<ProductMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [rating, setRating] = useState<FeedbackRating | ''>('NOT_HELPFUL');
  const [page, setPage] = useState(0);
  const [feedback, setFeedback] = useState<Paged<FeedbackItem> | null>(null);
  const [detail, setDetail] = useState<FeedbackDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [m, f] = await Promise.all([
        api.productMetrics(days),
        api.metricsFeedback({ rating: rating || undefined, page, size: 10 }),
      ]);
      setMetrics(m);
      setFeedback(f);
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '제품 지표를 불러오지 못했습니다.');
    }
  }, [days, rating, page]);

  useEffect(() => { void load(); }, [load]);

  async function openDetail(messageId: number) {
    setDetailError(null);
    setDetail(null);
    try {
      setDetail(await api.metricsFeedbackDetail(messageId));
    } catch (e) {
      setDetailError(e instanceof HttpError ? e.body.message : '평가 상세를 불러오지 못했습니다.');
    }
  }

  const q = metrics?.answerQuality;
  const s = metrics?.sources;
  const fb = metrics?.feedback;
  const lat = metrics?.latency;
  const ad = metrics?.adoption;

  return (
    <div style={styles.wrap}>
      {error && <div style={styles.error}>{error}</div>}

      <section style={styles.card}>
        <header style={styles.head}>
          <b style={{ fontSize: 14 }}>제품 지표</b>
          <span style={styles.sub}>핵심 지표 기준</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
            {PERIODS.map((p) => (
              <Button key={p.days} size="sm"
                      variant={days === p.days ? 'primary' : 'default'}
                      onClick={() => setDays(p.days)}>{p.label}</Button>
            ))}
          </div>
        </header>

        {metrics && metrics.degraded.length > 0 && (
          <div style={styles.warn}>
            일부 구획을 읽지 못해 0으로 표시됩니다: <b>{metrics.degraded.join(', ')}</b>.
            해당 카드의 수치는 신뢰할 수 없습니다.
          </div>
        )}

        {metrics && (
          <div style={styles.grid}>
            <RatioCard
              label="출처 포함 답변 성공률"
              hint="상태가 정상이고 출처를 1건 이상 인용한 답변"
              numerator={q!.okWithSources}
              denominator={q!.assistantMessages}
              target={METRIC_TARGETS.answerSuccess}
              unitLabel="답변"
              degraded={metrics.degraded.includes('answerQuality')}
            />
            <RatioCard
              label="출처 검증 가능 비율"
              hint="원본 파일을 내려받을 수 있는 출처"
              numerator={s!.withOriginalFile}
              denominator={s!.citedSources}
              target={METRIC_TARGETS.sourceVerifiable}
              unitLabel="출처"
              degraded={metrics.degraded.includes('sources')}
            />
            <RatioCard
              label="유용 평가 비율"
              hint={`평가 응답률 ${ratioText(fb!.responded, fb!.assistantMessages)} (${fb!.responded}/${fb!.assistantMessages}건)`}
              numerator={fb!.helpful}
              denominator={fb!.responded}
              target={METRIC_TARGETS.helpful}
              unitLabel="평가"
              degraded={metrics.degraded.includes('feedback')}
            />
            <RatioCard
              label="질문한 사용자 비율"
              hint={`기간 내 질문 ${ad!.questions}건`}
              numerator={ad!.askedUsers}
              denominator={ad!.activeUsers}
              target={METRIC_TARGETS.adoption}
              unitLabel="활성 계정"
              degraded={metrics.degraded.includes('adoption')}
            />
          </div>
        )}
      </section>

      {metrics && (
        <div style={styles.row}>
          <section style={{ ...styles.card, flex: 1 }}>
            <header style={styles.head}>
              <b style={{ fontSize: 14 }}>응답 시간</b>
              <span style={styles.sub}>목표 {lat!.targetMs / 1000}초 · 타임라인 질문은 제외</span>
            </header>
            {lat!.measured === 0 ? (
              <div style={styles.empty}>측정된 답변이 없습니다.</div>
            ) : (
              <div style={styles.statRow}>
                <Stat label="평균" value={msText(lat!.averageMs)} />
                <Stat label="95 분위" value={msText(lat!.p95Ms)} />
                <Stat label={`${lat!.targetMs / 1000}초 초과`} value={`${lat!.overTargetCount}건`}
                      tone={lat!.overTargetCount > 0 ? 'warn' : undefined} />
                <Stat label="측정 건수" value={`${lat!.measured}건`} />
              </div>
            )}
          </section>

          <section style={{ ...styles.card, flex: 1 }}>
            <header style={styles.head}>
              <b style={{ fontSize: 14 }}>답변 상태 분해</b>
              <span style={styles.sub}>성공률이 낮을 때 원인을 가른다</span>
            </header>
            {q!.byStatus.length === 0 ? (
              <div style={styles.empty}>답변이 없습니다.</div>
            ) : (
              <ul style={styles.list}>
                {q!.byStatus.map((row) => (
                  <li key={row.status} style={styles.listRow}>
                    <span>{ANSWER_STATUS_LABEL[row.status] ?? row.status}</span>
                    <b style={styles.listValue}>{row.count}건</b>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}

      {metrics && (
        <section style={styles.card}>
          <header style={styles.head}>
            <b style={{ fontSize: 14 }}>권한 차단</b>
            <span style={styles.sub}>
              차단이 동작한 기록이다 — 노출 건수가 아니다. 노출이 없었음은 로그로 증명할 수 없다
            </span>
          </header>
          <div style={styles.denialBody}>
            <Stat label="기간 내 차단" value={`${metrics.denials.total}건`} />
            {metrics.denials.byAction.length > 0 && (
              <div style={styles.chipRow}>
                {metrics.denials.byAction.map((a) => (
                  <span key={a.action} style={styles.chip}>
                    <span style={styles.chipName}>{a.action}</span>
                    <span style={styles.chipCount}>{a.count}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      <section style={styles.card}>
        <header style={styles.head}>
          <b style={{ fontSize: 14 }}>평가가 달린 답변</b>
          <span style={styles.sub}>숫자에서 실제 질문·근거까지 내려가 본다</span>
          <select value={rating} style={{ ...styles.select, marginLeft: 'auto' }}
                  onChange={(e) => { setRating(e.target.value as FeedbackRating | ''); setPage(0); }}>
            <option value="NOT_HELPFUL">도움 안 됨</option>
            <option value="HELPFUL">도움 됨</option>
            <option value="">전체</option>
          </select>
        </header>

        {feedback && feedback.content.length === 0 && (
          <div style={styles.empty}>해당하는 평가가 없습니다.</div>
        )}

        {feedback && feedback.content.length > 0 && (
          <>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>평가</th>
                  <th style={styles.th}>질문</th>
                  <th style={styles.th}>상태</th>
                  <th style={styles.th}>출처</th>
                  <th style={styles.th}>남긴 사유</th>
                  <th style={styles.th} />
                </tr>
              </thead>
              <tbody>
                {feedback.content.map((item) => (
                  <tr key={item.messageId}>
                    <td style={styles.td}>
                      <Badge tone={item.rating === 'HELPFUL' ? 'ok' : 'err'}>
                        {item.rating === 'HELPFUL' ? '도움' : '미흡'}
                      </Badge>
                    </td>
                    <td style={styles.td}>{item.question ?? <i style={styles.muted}>질문 없음</i>}</td>
                    <td style={styles.td}>
                      {ANSWER_STATUS_LABEL[item.answerStatus ?? 'UNKNOWN'] ?? item.answerStatus}
                    </td>
                    <td style={styles.td}>{item.sourceCount}건</td>
                    <td style={styles.td}>{item.reason ?? <span style={styles.muted}>—</span>}</td>
                    <td style={styles.td}>
                      <Button size="sm" onClick={() => void openDetail(item.messageId)}>열기</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={styles.foot}>
              <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>이전</Button>
              <span style={styles.pageInfo}>
                {feedback.page + 1} / {Math.max(feedback.totalPages, 1)} · 전체 {feedback.totalElements}건
              </span>
              <Button size="sm" disabled={feedback.page + 1 >= feedback.totalPages}
                      onClick={() => setPage((p) => p + 1)}>다음</Button>
            </div>
          </>
        )}
      </section>

      {detailError && <div style={styles.error}>{detailError}</div>}
      {detail && <FeedbackDetailView detail={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

/** 분자·분모를 함께 보여준다. 분모가 작으면 목표 달성 판정을 하지 않는다. */
function RatioCard({ label, hint, numerator, denominator, target, unitLabel, degraded }: {
  label: string;
  hint: string;
  numerator: number;
  denominator: number;
  target: number;
  unitLabel: string;
  degraded: boolean;
}) {
  const enough = denominator >= MIN_SAMPLE;
  const ratio = denominator === 0 ? null : numerator / denominator;
  const met = ratio !== null && ratio >= target;

  return (
    <div style={styles.ratioCard}>
      <div style={styles.ratioLabel}>{label}</div>
      <div style={{ ...styles.ratioValue, color: degraded ? 'var(--ink-3)' : undefined }}>
        {ratio === null ? '—' : `${(ratio * 100).toFixed(1)}%`}
      </div>
      <div style={styles.ratioDenom}>
        {numerator} / {denominator} {unitLabel}
      </div>
      <div style={styles.ratioHint}>{hint}</div>
      <div style={styles.ratioFoot}>
        <span style={styles.ratioTarget}>목표 {(target * 100).toFixed(0)}%</span>
        {degraded ? (
          <Badge tone="neutral">읽기 실패</Badge>
        ) : !enough ? (
          <Badge tone="neutral">표본 부족</Badge>
        ) : (
          <Badge tone={met ? 'ok' : 'warn'}>{met ? '달성' : '미달'}</Badge>
        )}
      </div>
    </div>
  );
}

/** 부정 평가의 원인을 찾으려면 질문·재작성 질문·답변 전문·인용 출처가 한 자리에 있어야 한다. */
function FeedbackDetailView({ detail, onClose }: { detail: FeedbackDetail; onClose: () => void }) {
  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <b style={{ fontSize: 14 }}>평가 상세 · 메시지 #{detail.messageId}</b>
        <Badge tone={detail.rating === 'HELPFUL' ? 'ok' : 'err'}>
          {detail.rating === 'HELPFUL' ? '도움 됨' : '도움 안 됨'}
        </Badge>
        <Button size="sm" onClick={onClose} style={{ marginLeft: 'auto' }}>닫기</Button>
      </header>

      <div style={styles.detailBody}>
        <Field label="평가 사유">{detail.reason ?? <span style={styles.muted}>남기지 않음</span>}</Field>
        <Field label="질문">{detail.question ?? <span style={styles.muted}>질문을 찾지 못했다</span>}</Field>
        {detail.rewrittenQuery && detail.rewrittenQuery !== detail.question && (
          <Field label="검색에 쓴 질문">
            {detail.rewrittenQuery}
            <div style={styles.fieldHint}>
              대화 맥락을 합쳐 재작성한 질문이다. 원래 질문과 뜻이 달라졌으면 그게 원인일 수 있다
            </div>
          </Field>
        )}
        <Field label="답변">
          <div style={styles.answer}>{detail.answer}</div>
        </Field>
        <Field label="처리 정보">
          <span style={styles.metaLine}>
            상태 {ANSWER_STATUS_LABEL[detail.answerStatus ?? 'UNKNOWN'] ?? detail.answerStatus}
            {' · '}모델 {detail.modelName ?? '미기록'}
            {' · '}응답 {msText(detail.latencyMs)}
            {' · '}평가 {new Date(detail.createdAt).toLocaleString('ko-KR')}
            {detail.raterName ? ` · 평가자 ${detail.raterName}` : ''}
          </span>
        </Field>
        <Field label={`인용한 출처 ${detail.sources.length}건`}>
          {detail.sources.length === 0 ? (
            <span style={styles.muted}>인용한 출처가 없다 — 근거 없이 답했거나 검색이 비었다</span>
          ) : (
            <ol style={styles.sourceList}>
              {detail.sources.map((src) => (
                <li key={`${src.documentId}-${src.chunkNo}`} style={styles.sourceRow}>
                  <b>{src.documentTitle}</b>
                  <span style={styles.sourceMeta}>
                    v{src.documentVersion} · 청크 {src.chunkNo}
                    {src.sectionPath ? ` · ${src.sectionPath}` : ''}
                    {' · '}
                    {src.similarity != null ? `유사도 ${src.similarity.toFixed(3)}` : '키워드 일치'}
                  </span>
                  {!src.hasOriginalFile && <Badge tone="neutral">원본 파일 없음</Badge>}
                </li>
              ))}
            </ol>
          )}
        </Field>
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={styles.fieldLabel}>{label}</div>
      <div style={styles.fieldValue}>{children}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'warn' }) {
  return (
    <div style={styles.stat}>
      <div style={{ ...styles.statValue, color: tone === 'warn' ? 'var(--err)' : undefined }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  );
}

function msText(ms: number | null): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}초` : `${ms}ms`;
}

function ratioText(numerator: number, denominator: number): string {
  if (denominator === 0) return '—';
  return `${((numerator / denominator) * 100).toFixed(0)}%`;
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 16 },
  row: { display: 'flex', gap: 16, flexWrap: 'wrap' },
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden', minWidth: 280 },
  head: { display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  sub: { fontSize: 11.5, color: 'var(--ink-3)' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 1, background: 'var(--rule-soft)' },
  ratioCard: { background: 'var(--paper)', padding: '16px' },
  ratioLabel: { fontSize: 12, fontWeight: 700, color: 'var(--ink-2)' },
  ratioValue: { fontSize: 30, fontWeight: 800, marginTop: 6, lineHeight: 1.1 },
  ratioDenom: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 2 },
  ratioHint: { fontSize: 11, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.5 },
  ratioFoot: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 },
  ratioTarget: { fontSize: 11, color: 'var(--ink-3)' },
  statRow: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(90px, 1fr))', gap: 1, background: 'var(--rule-soft)' },
  stat: { background: 'var(--paper)', padding: '16px 12px', textAlign: 'center' },
  statValue: { fontSize: 20, fontWeight: 800 },
  statLabel: { fontSize: 11, color: 'var(--ink-3)', marginTop: 4 },
  list: { listStyle: 'none', margin: 0, padding: 0 },
  listRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid var(--rule-soft)', fontSize: 12.5 },
  listValue: { fontVariantNumeric: 'tabular-nums' },
  denialBody: { display: 'flex', alignItems: 'center', gap: 20, padding: '12px 16px', flexWrap: 'wrap' },
  chipRow: { display: 'flex', flexWrap: 'wrap', gap: 6 },
  chip: { display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', border: '1px solid var(--rule)', borderRadius: 6 },
  chipName: { fontSize: 11.5, fontFamily: 'var(--mono)' },
  chipCount: { fontSize: 11.5, fontWeight: 700 },
  select: { padding: '6px 10px', border: '1px solid var(--rule)', borderRadius: 6, background: 'var(--paper)', font: 'inherit', fontSize: 12.5 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5, tableLayout: 'fixed' },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '9px 14px', borderBottom: '1px solid var(--rule-soft)', overflow: 'hidden', textOverflow: 'ellipsis' },
  muted: { color: 'var(--ink-3)' },
  foot: { display: 'flex', alignItems: 'center', gap: 12, padding: 14, justifyContent: 'center' },
  pageInfo: { fontSize: 12, color: 'var(--ink-3)' },
  detailBody: { display: 'flex', flexDirection: 'column', gap: 14, padding: 16 },
  fieldLabel: { fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.04em', marginBottom: 4 },
  fieldValue: { fontSize: 13, lineHeight: 1.6 },
  fieldHint: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 4 },
  answer: { whiteSpace: 'pre-wrap', padding: '10px 12px', background: 'var(--rule-soft)', borderRadius: 6 },
  metaLine: { fontSize: 12, color: 'var(--ink-2)' },
  sourceList: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 8 },
  sourceRow: { display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12.5 },
  sourceMeta: { fontSize: 11.5, color: 'var(--ink-3)' },
  empty: { padding: 28, textAlign: 'center', color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  warn: { margin: 14, padding: '10px 12px', background: 'var(--warn-bg)', color: 'var(--ink-2)', borderRadius: 6, fontSize: 12.5 },
};
