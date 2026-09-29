import { useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  HEALTH_STATUS_LABEL,
  type ComponentHealth, type DlqEntry, type DlqStatus, type HealthStatus, type OpsError,
  type OpsHealth, type OpsIndexStatus, type Paged, type TraceSpan,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const STATUS_TONE: Record<HealthStatus, 'ok' | 'warn' | 'err' | 'neutral'> = {
  UP: 'ok', DEGRADED: 'warn', DOWN: 'err', UNKNOWN: 'neutral',
};

const INDEX_LAG_WARN_SECONDS = 300;

/** 운영 현황. 시스템 관리자만 본다. */
export function OpsPanel() {
  const [health, setHealth] = useState<OpsHealth | null>(null);
  const [indexStatus, setIndexStatus] = useState<OpsIndexStatus | null>(null);
  const [errors, setErrors] = useState<Paged<OpsError> | null>(null);
  const [errorPage, setErrorPage] = useState(0);
  const [serviceFilter, setServiceFilter] = useState('');
  const [error, setError] = useState<string | null>(null);

  const [traceId, setTraceId] = useState('');
  const [traceSpans, setTraceSpans] = useState<TraceSpan[] | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [tracing, setTracing] = useState(false);

  const [dlq, setDlq] = useState<DlqStatus[] | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [h, i, e, d] = await Promise.all([
        api.opsHealth(),
        api.opsIndexStatus(),
        api.opsErrors({ service: serviceFilter || undefined, page: errorPage, size: 20 }),
        api.opsDlqStatuses(),
      ]);
      setHealth(h);
      setIndexStatus(i);
      setErrors(e);
      setDlq(d);
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '운영 현황을 불러오지 못했습니다.');
    }
  }, [serviceFilter, errorPage]);

  useEffect(() => { void load(); }, [load]);

  async function runTrace() {
    if (!traceId.trim()) return;
    setTracing(true);
    setTraceError(null);
    setTraceSpans(null);
    try {
      setTraceSpans(await api.opsTrace(traceId.trim()));
    } catch (e) {
      setTraceError(e instanceof HttpError ? e.body.message : '추적 정보를 불러오지 못했습니다.');
    } finally {
      setTracing(false);
    }
  }

  return (
    <div style={styles.wrap}>
      {error && <div style={styles.error}>{error}</div>}

      <section style={styles.card}>
        <header style={styles.head}>
          <b style={{ fontSize: 14 }}>저장소 · 서비스 상태</b>
          {health && <span style={styles.checkedAt}>확인 시각 {new Date(health.checkedAt).toLocaleTimeString('ko-KR')}</span>}
          <Button size="sm" onClick={load} style={{ marginLeft: 'auto' }}>새로 고침</Button>
        </header>
        {health && (
          <div style={styles.healthBody}>
            <ComponentGroup title="저장소" items={health.storages} />
            <ComponentGroup title="서비스" items={health.services} />
          </div>
        )}
      </section>

      <section style={styles.card}>
        <header style={styles.head}><b style={{ fontSize: 14 }}>색인 현황</b></header>
        {indexStatus && (
          <div style={styles.indexGrid}>
            <Stat label="대기" value={indexStatus.pendingCount} />
            <Stat label="처리중" value={indexStatus.runningCount} />
            <Stat label="실패" value={indexStatus.failCount} tone={indexStatus.failCount > 0 ? 'err' : undefined} />
            <Stat
              label="가장 오래된 대기"
              value={`${indexStatus.indexLagSeconds}초`}
              tone={indexStatus.indexLagSeconds > INDEX_LAG_WARN_SECONDS ? 'err' : undefined}
            />
          </div>
        )}
      </section>

      <section style={styles.card}>
        <header style={styles.head}>
          <b style={{ fontSize: 14 }}>처리 실패 큐 (DLQ)</b>
          <span style={styles.checkedAt}>전달 한도 초과·영구 실패로 옮겨진 이벤트</span>
        </header>
        {dlq && <DlqBody statuses={dlq} onChanged={load} />}
      </section>

      <section style={styles.card}>
        <header style={styles.head}>
          <b style={{ fontSize: 14 }}>최근 오류</b>
          <select value={serviceFilter} style={{ ...styles.select, marginLeft: 'auto', width: 160 }}
                  onChange={(e) => { setServiceFilter(e.target.value); setErrorPage(0); }}>
            <option value="">전체 서비스</option>
            <option value="auth-service">auth-service</option>
            <option value="index-worker">index-worker</option>
          </select>
        </header>
        {errors && errors.content.length === 0 && <div style={styles.empty}>최근 오류가 없습니다.</div>}
        {errors && errors.content.length > 0 && (
          <>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>시각</th>
                  <th style={styles.th}>서비스</th>
                  <th style={styles.th}>메시지</th>
                  <th style={styles.th}>추적 ID</th>
                </tr>
              </thead>
              <tbody>
                {errors.content.map((e, idx) => (
                  <tr key={idx}>
                    <td style={styles.td}>{new Date(e.occurredAt).toLocaleString('ko-KR')}</td>
                    <td style={styles.td}><Badge tone="accent">{e.service}</Badge></td>
                    <td style={styles.td}>{e.message}</td>
                    <td style={{ ...styles.td, fontFamily: 'var(--mono)', fontSize: 11 }}>
                      {e.correlationId
                        ? <button style={styles.linkButton} onClick={() => { setTraceId(e.correlationId!); void runTrace(); }}>{e.correlationId}</button>
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <footer style={styles.foot}>
              <Button size="sm" disabled={errorPage === 0} onClick={() => setErrorPage((p) => p - 1)}>이전</Button>
              <span style={styles.pageInfo}>
                {errorPage + 1} / {Math.max(errors.totalPages, 1)} · 전체 {errors.totalElements}건
              </span>
              <Button size="sm" disabled={errorPage + 1 >= errors.totalPages} onClick={() => setErrorPage((p) => p + 1)}>다음</Button>
            </footer>
          </>
        )}
      </section>

      <section style={styles.card}>
        <header style={styles.head}><b style={{ fontSize: 14 }}>요청 추적</b></header>
        <div style={{ padding: 16 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <input
              value={traceId}
              placeholder="상관관계 ID (예: req_0c81f4a2)"
              onChange={(e) => setTraceId(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void runTrace(); }}
              style={{ flex: 1 }}
            />
            <Button variant="primary" disabled={tracing} onClick={runTrace}>
              {tracing ? '조회 중…' : '조회'}
            </Button>
          </div>
          <div style={styles.hint}>
            현재는 auth-service 의 감사 로그에 남은 구간만 보여준다. 이 프로젝트에 아직 중앙
            로그 수집소가 없어 다른 서비스의 처리 구간은 포함되지 않는다.
          </div>
          {traceError && <div style={styles.error}>{traceError}</div>}
          {traceSpans && traceSpans.length > 0 && (
            <table style={{ ...styles.table, marginTop: 12 }}>
              <thead>
                <tr>
                  <th style={styles.th}>서비스</th>
                  <th style={styles.th}>동작</th>
                  <th style={styles.th}>시각</th>
                  <th style={styles.th}>결과</th>
                  <th style={styles.th}>메시지</th>
                </tr>
              </thead>
              <tbody>
                {traceSpans.map((s, idx) => (
                  <tr key={idx}>
                    <td style={styles.td}>{s.service}</td>
                    <td style={styles.td}>{s.operation ?? '—'}</td>
                    <td style={styles.td}>{new Date(s.startedAt).toLocaleString('ko-KR')}</td>
                    <td style={styles.td}>
                      <Badge tone={s.status === 'OK' ? 'ok' : 'err'}>{s.status === 'OK' ? '허용' : '오류'}</Badge>
                    </td>
                    <td style={styles.td}>{s.message ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * 스트림별 DLQ 건수와 항목, 재처리·폐기.
 *
 * 재처리는 원본 스트림에 되돌리는 것뿐이고, 실제 성공 여부는 컨슈머가 다시 처리해 봐야
 * 안다 — 같은 이유로 또 실패하면 다시 DLQ 로 돌아온다. 그래서 여기서는 "되돌렸다"까지만
 * 알리고, 결과는 새로 고침한 건수로 확인하게 한다.
 */
function DlqBody({ statuses, onChanged }: { statuses: DlqStatus[]; onChanged: () => void }) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [entries, setEntries] = useState<DlqEntry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggleOpen(key: string) {
    setMessage(null);
    setError(null);
    setSelected(new Set());
    if (openKey === key) {
      setOpenKey(null);
      setEntries([]);
      return;
    }
    setOpenKey(key);
    setEntries([]);
    try {
      setEntries(await api.opsDlqEntries(key, 50));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : 'DLQ 항목을 불러오지 못했습니다.');
    }
  }

  function toggleOne(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function act(kind: 'replay' | 'discard') {
    if (!openKey || selected.size === 0) return;
    if (kind === 'discard' && !window.confirm(
      `${selected.size}건을 폐기합니다. 원본 스트림에 되돌리지 않고 지우므로 그 이벤트는 영구히 유실됩니다. 계속할까요?`
    )) return;

    setBusy(true);
    setMessage(null);
    setError(null);
    const ids = [...selected];
    try {
      const result = kind === 'replay'
        ? await api.opsDlqReplay(openKey, ids)
        : await api.opsDlqDiscard(openKey, ids);
      const verb = kind === 'replay' ? '재처리' : '폐기';
      setMessage(result.failedIds.length === 0
        ? `${result.succeeded}건 ${verb} 요청을 보냈습니다.`
        : `${result.succeeded}건 ${verb}, ${result.failedIds.length}건 실패(${result.failedIds.join(', ')}).`);
      // 실패한 것만 선택으로 남겨 바로 다시 시도할 수 있게 한다
      setSelected(new Set(result.failedIds));
      setEntries(await api.opsDlqEntries(openKey, 50));
      onChanged();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '요청을 처리하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  const total = statuses.reduce((sum, s) => sum + s.count, 0);

  return (
    <div>
      <div style={styles.dlqRows}>
        {statuses.map((s) => (
          <div key={s.key} style={styles.dlqRow}>
            <Badge tone={s.count > 0 ? 'err' : 'ok'}>{s.count}건</Badge>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 700 }}>{s.stream}</div>
              <div style={{ fontSize: 11, color: 'var(--ink-3)' }}>
                소비자 {s.consumerService}
                {s.oldestAt && ` · 가장 오래된 항목 ${new Date(s.oldestAt).toLocaleString('ko-KR')}`}
              </div>
            </div>
            <Button size="sm" style={{ marginLeft: 'auto' }} disabled={s.count === 0}
                    onClick={() => void toggleOpen(s.key)}>
              {openKey === s.key ? '접기' : '항목 보기'}
            </Button>
          </div>
        ))}
      </div>

      {total === 0 && <div style={styles.empty}>유실된 이벤트가 없습니다.</div>}
      {error && <div style={styles.error}>{error}</div>}
      {message && <div style={styles.notice}>{message}</div>}

      {openKey && entries.length > 0 && (
        <>
          <div style={styles.dlqToolbar}>
            <span style={{ fontSize: 12, color: 'var(--ink-3)' }}>{selected.size}건 선택</span>
            <Button size="sm" variant="primary" disabled={busy || selected.size === 0}
                    onClick={() => void act('replay')}>선택 재처리</Button>
            <Button size="sm" disabled={busy || selected.size === 0}
                    onClick={() => void act('discard')}>선택 폐기</Button>
          </div>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={{ ...styles.th, width: 36 }} />
                <th style={styles.th}>옮겨진 시각</th>
                <th style={styles.th}>전달</th>
                <th style={styles.th}>사유</th>
                <th style={styles.th}>본문</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td style={styles.td}>
                    <input type="checkbox" checked={selected.has(e.id)} onChange={() => toggleOne(e.id)}
                           aria-label={`${e.id} 선택`} />
                  </td>
                  <td style={styles.td}>
                    {e.addedAt ? new Date(e.addedAt).toLocaleString('ko-KR') : '—'}
                  </td>
                  <td style={styles.td}>{e.deliveries}회</td>
                  <td style={styles.td}>{e.reason ?? '—'}</td>
                  <td style={{ ...styles.td, fontFamily: 'var(--mono)', fontSize: 11 }}>
                    {Object.entries(e.payload).map(([k, v]) => `${k}=${v}`).join(' ') || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function ComponentGroup({ title, items }: { title: string; items: ComponentHealth[] }) {
  return (
    <div>
      <div style={styles.groupLabel}>{title}</div>
      <div style={styles.chipRow}>
        {items.map((c) => (
          <div key={c.name} style={styles.chip}>
            <Badge tone={STATUS_TONE[c.status]}>{HEALTH_STATUS_LABEL[c.status]}</Badge>
            <span style={styles.chipName}>{c.name}</span>
            {c.latencyMs !== null && <span style={styles.chipLatency}>{c.latencyMs}ms</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'err' }) {
  return (
    <div style={styles.stat}>
      <div style={{ ...styles.statValue, color: tone === 'err' ? 'var(--err)' : 'var(--ink)' }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'flex', flexDirection: 'column', gap: 16 },
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  checkedAt: { fontSize: 11.5, color: 'var(--ink-3)' },
  healthBody: { padding: 16, display: 'grid', gap: 16 },
  groupLabel: { fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.06em', marginBottom: 8 },
  chipRow: { display: 'flex', flexWrap: 'wrap', gap: 8 },
  chip: { display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', border: '1px solid var(--rule)', borderRadius: 8 },
  chipName: { fontSize: 12.5, fontWeight: 600 },
  chipLatency: { fontSize: 11, color: 'var(--ink-3)' },
  indexGrid: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, background: 'var(--rule-soft)' },
  stat: { background: 'var(--paper)', padding: '18px 16px', textAlign: 'center' },
  statValue: { fontSize: 24, fontWeight: 800 },
  statLabel: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 4 },
  select: { padding: '6px 10px', border: '1px solid var(--rule)', borderRadius: 6, background: 'var(--paper)', font: 'inherit', fontSize: 12.5 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '9px 14px', borderBottom: '1px solid var(--rule-soft)' },
  linkButton: { background: 'none', border: 'none', padding: 0, color: 'var(--accent)', font: 'inherit', fontFamily: 'var(--mono)', fontSize: 11, cursor: 'pointer', textDecoration: 'underline' },
  foot: { display: 'flex', alignItems: 'center', gap: 12, padding: 14, justifyContent: 'center' },
  pageInfo: { fontSize: 12, color: 'var(--ink-3)' },
  hint: { fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.6 },
  empty: { padding: 28, textAlign: 'center', color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  notice: { margin: 14, padding: '10px 12px', background: 'var(--rule-soft)', color: 'var(--ink-2)', borderRadius: 6, fontSize: 13 },
  dlqRows: { display: 'flex', flexDirection: 'column' },
  dlqRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: '1px solid var(--rule-soft)' },
  dlqToolbar: { display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', background: '#fbfcfd', borderTop: '1px solid var(--rule)', borderBottom: '1px solid var(--rule)' },
};
