import { useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  DECISION_STATUS_LABEL, DOCUMENT_TYPE_LABEL,
  type DecisionStatus, type DocumentPage, type DocumentType,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';

const TYPES = Object.keys(DOCUMENT_TYPE_LABEL) as DocumentType[];
const STATUSES = Object.keys(DECISION_STATUS_LABEL) as DecisionStatus[];

const STATUS_TONE: Record<DecisionStatus, 'ok' | 'warn' | 'err' | 'neutral'> = {
  CURRENT: 'ok', CHANGED: 'warn', DEPRECATED: 'err', UNDECIDED: 'neutral',
};

interface Props {
  onOpen: (id: number) => void;
  onCreate: () => void;
  onImport: () => void;
  reloadKey: number;
}

/** 문서 목록. 권한 범위 안의 문서만 나타나며 건수도 범위 안에서 집계된다. */
export function DocumentList({ onOpen, onCreate, onImport, reloadKey }: Props) {
  const [data, setData] = useState<DocumentPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [types, setTypes] = useState<DocumentType[]>([]);
  const [statuses, setStatuses] = useState<DecisionStatus[]>([]);
  // 내가 쓴 문서만. 서버가 요청자 id 로 바꿔 거른다 — 프론트가 id 를 보내지 않는다.
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.documents({
        q, documentType: types, decisionStatus: statuses, mine: mine || undefined, page, size: 20,
      }));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '목록을 불러오지 못했습니다.');
    }
  }, [q, types, statuses, mine, page]);

  useEffect(() => { void load(); }, [load, reloadKey]);

  function toggle<T>(list: T[], value: T, set: (v: T[]) => void) {
    set(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
    setPage(0);
  }

  return (
    <div style={styles.wrap}>
      <aside style={styles.side}>
        <div style={styles.sideLabel}>문서 유형</div>
        {TYPES.map((t) => (
          <label key={t} style={styles.check}>
            <input type="checkbox" checked={types.includes(t)} onChange={() => toggle(types, t, setTypes)}
                   style={{ width: 'auto', marginRight: 8 }} />
            {DOCUMENT_TYPE_LABEL[t]}
            <span style={styles.count}>{data?.typeCounts?.[t] ?? 0}</span>
          </label>
        ))}

        <div style={{ ...styles.sideLabel, marginTop: 20 }}>작성자</div>
        <label style={styles.check}>
          <input type="checkbox" checked={mine} onChange={() => { setMine(!mine); setPage(0); }}
                 style={{ width: 'auto', marginRight: 8 }} />
          내가 쓴 문서만
        </label>

        <div style={{ ...styles.sideLabel, marginTop: 20 }}>결정 상태</div>
        {STATUSES.map((s) => (
          <label key={s} style={styles.check}>
            <input type="checkbox" checked={statuses.includes(s)} onChange={() => toggle(statuses, s, setStatuses)}
                   style={{ width: 'auto', marginRight: 8 }} />
            {DECISION_STATUS_LABEL[s]}
          </label>
        ))}
      </aside>

      <section style={styles.main}>
        <header style={styles.head}>
          <input
            value={q}
            placeholder="제목 · 작성자 · 관련 프로젝트로 검색"
            onChange={(e) => { setQ(e.target.value); setPage(0); }}
            style={{ flex: 1 }}
          />
          <Button onClick={onImport}>일괄 가져오기</Button>
          <Button variant="primary" onClick={onCreate}>새 문서</Button>
        </header>

        {error && <div style={styles.error}>{error}</div>}

        {data && data.content.length === 0 && !error && (
          <div style={styles.empty}>
            {mine ? '내가 쓴 문서가 없습니다.' : '권한 범위 안에 문서가 없습니다.'}<br />
            <span style={{ fontSize: 12 }}>
              {mine
                ? '"내가 쓴 문서만" 필터를 해제하면 권한 범위 안의 전체 문서를 볼 수 있습니다.'
                : '새 문서를 등록하거나 관리자에게 접근 권한을 요청하세요.'}
            </span>
          </div>
        )}

        {data && data.content.length > 0 && (
          <>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={{ ...styles.th, width: '40%' }}>제목</th>
                  <th style={styles.th}>유형</th>
                  <th style={styles.th}>작성자</th>
                  <th style={styles.th}>작성일</th>
                  <th style={styles.th}>결정 상태</th>
                  <th style={styles.th}>원본</th>
                </tr>
              </thead>
              <tbody>
                {data.content.map((d) => (
                  <tr key={d.id} onClick={() => onOpen(d.id)} style={styles.row}>
                    <td style={styles.td}>
                      <b>{d.title}</b>
                      {d.projectName && <div style={styles.project}>{d.projectName}</div>}
                    </td>
                    <td style={styles.td}><Badge tone="accent">{DOCUMENT_TYPE_LABEL[d.documentType]}</Badge></td>
                    <td style={styles.td}>{d.authorName}</td>
                    <td style={styles.td}>{d.authoredAt}</td>
                    <td style={styles.td}>
                      <Badge tone={STATUS_TONE[d.decisionStatus]}>{DECISION_STATUS_LABEL[d.decisionStatus]}</Badge>
                    </td>
                    <td style={styles.td}>
                      {d.hasFile ? <Badge tone="ok">있음</Badge> : <span style={{ color: 'var(--rule)' }}>—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <footer style={styles.foot}>
              <Button size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>이전</Button>
              <span style={styles.pageInfo}>
                {page + 1} / {Math.max(data.totalPages, 1)} · 전체 {data.totalElements}건
              </span>
              <Button size="sm" disabled={page + 1 >= data.totalPages} onClick={() => setPage((p) => p + 1)}>다음</Button>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'grid', gridTemplateColumns: '190px 1fr', gap: 18, alignItems: 'start' },
  side: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, padding: 16 },
  sideLabel: { fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', letterSpacing: '.06em', marginBottom: 8 },
  check: { display: 'flex', alignItems: 'center', fontSize: 12.5, padding: '4px 0', cursor: 'pointer' },
  count: { marginLeft: 'auto', color: 'var(--ink-3)', fontSize: 11.5 },
  main: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', gap: 10, padding: 16, borderBottom: '1px solid var(--rule)' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: {
    textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)',
    padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap',
  },
  row: { cursor: 'pointer' },
  td: { padding: '11px 14px', borderBottom: '1px solid var(--rule-soft)', verticalAlign: 'middle' },
  project: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 2 },
  foot: { display: 'flex', alignItems: 'center', gap: 12, padding: 14, justifyContent: 'center' },
  pageInfo: { fontSize: 12, color: 'var(--ink-3)' },
  error: { margin: 16, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  empty: { padding: '48px 20px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 13.5, lineHeight: 1.8 },
};
