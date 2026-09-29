import { useEffect, useRef, useState, useMemo } from 'react';
import { api, HttpError } from '../../api/client';
import type { ConflictItem, ConflictStatus, CurrentUser } from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { ConflictActionModal } from './ConflictActionModal';
import { BulkConflictActionModal } from './BulkConflictActionModal';

interface Props {
  user: CurrentUser;
  onOpenDocument: (documentId: number) => void;
  /** 알림 링크(/governance/conflicts/{id})로 들어온 경우 그 상충으로 스크롤하고 강조한다. */
  focusConflictId?: number | null;
  onFocusConsumed?: () => void;
}

// 심각도 구간. governance-worker의 notifications/models.py(severity_label)와 값을 맞춘다 —
// 다르면 같은 점수가 알림과 화면에서 다른 등급으로 보인다
const SEVERITY_HIGH = 0.85;
const SEVERITY_MEDIUM = 0.7;

export function GovernanceDashboard({ user, onOpenDocument, focusConflictId, onFocusConsumed }: Props) {
  const [conflicts, setConflicts] = useState<ConflictItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 필터 상태
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [severityFilter, setSeverityFilter] = useState<string>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  // 내 문서가 한쪽에라도 걸린 상충만. author_id 는 목록 DTO 에 없어
  // 클라이언트에서 거를 수 없다 — 서버 필터라 켜고 끌 때 다시 불러온다.
  const [mineOnly, setMineOnly] = useState(false);

  // 모달 상태
  const [selectedConflict, setSelectedConflict] = useState<ConflictItem | null>(null);

  // 일괄 처리 선택 상태
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBulkModal, setShowBulkModal] = useState(false);

  // 알림 링크로 지목된 상충 강조
  const [highlightId, setHighlightId] = useState<number | null>(null);

  const isAdmin = user.roles.some((r) => r !== 'MEMBER');

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      // 페이지당 최대 100건 조회
      const res = await api.conflicts({ size: 100, mine: mineOnly || undefined });
      setConflicts(res.content);
    } catch (err: any) {
      setError(err?.message ?? '상충 목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
    // mineOnly 는 서버 필터라 값이 바뀌면 다시 불러온다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mineOnly]);

  // 통계 계산
  const stats = useMemo(() => {
    const total = conflicts.length;
    const open = conflicts.filter((c) => c.status === 'OPEN').length;
    const resolved = conflicts.filter((c) => c.status === 'RESOLVED').length;
    const dismissed = conflicts.filter((c) => c.status === 'DISMISSED').length;
    // 문서가 개정되어 governance-worker 가 자동으로 닫은 건(사람의 조치가 아니다)
    const superseded = conflicts.filter((c) => c.status === 'SUPERSEDED').length;
    const highRisk = conflicts.filter((c) => c.severityScore >= SEVERITY_HIGH && c.status === 'OPEN').length;
    return { total, open, resolved, dismissed, superseded, highRisk };
  }, [conflicts]);

  // 필터링 적용
  const filteredList = useMemo(() => {
    return conflicts.filter((c) => {
      if (statusFilter !== 'ALL' && c.status !== statusFilter) return false;
      if (severityFilter === 'CRITICAL' && c.severityScore < SEVERITY_HIGH) return false;
      if (severityFilter === 'HIGH' && (c.severityScore < SEVERITY_MEDIUM || c.severityScore >= SEVERITY_HIGH)) return false;
      if (severityFilter === 'MEDIUM' && c.severityScore >= SEVERITY_MEDIUM) return false;

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchTitle =
          c.sourceDocTitle.toLowerCase().includes(q) ||
          c.targetDocTitle.toLowerCase().includes(q);
        const matchReason = c.reasoning.toLowerCase().includes(q);
        const matchTeam = c.affectedTeams.some((t) => t.toLowerCase().includes(q));
        if (!matchTitle && !matchReason && !matchTeam) return false;
      }
      return true;
    });
  }, [conflicts, statusFilter, severityFilter, searchQuery]);

  // 알림 링크로 지목된 상충을 화면에 띄운다. 목록은 최근 100건만 불러오므로, 오래된
  // 알림에서 들어와 목록에 없으면 그 건만 단건으로 가져와 앞에 붙인다.
  const focusedRef = useRef<number | null>(null);
  useEffect(() => {
    if (focusConflictId == null || loading || focusedRef.current === focusConflictId) return;
    focusedRef.current = focusConflictId;
    const id = focusConflictId;
    let cancelled = false;

    (async () => {
      if (!conflicts.some((c) => c.id === id)) {
        try {
          const item = await api.conflict(id);
          if (cancelled) return;
          setConflicts((prev) => (prev.some((c) => c.id === item.id) ? prev : [item, ...prev]));
        } catch (err) {
          if (cancelled) return;
          setError(
            err instanceof HttpError
              ? `알림 링크의 상충 #${id}을 열 수 없습니다: ${err.body.message}`
              : `알림 링크의 상충 #${id}을 열 수 없습니다.`,
          );
          onFocusConsumed?.();
          return;
        }
      }
      if (cancelled) return;
      // 지목된 건이 필터에 가려지지 않도록 필터를 초기화한 뒤 강조한다
      setStatusFilter('ALL');
      setSeverityFilter('ALL');
      setSearchQuery('');
      setHighlightId(id);
      onFocusConsumed?.();
    })();

    return () => { cancelled = true; };
  }, [focusConflictId, loading, conflicts, onFocusConsumed]);

  useEffect(() => {
    if (highlightId == null) return;
    document.getElementById(`conflict-${highlightId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [highlightId]);

  function handleActionSuccess(updated: ConflictItem) {
    setConflicts((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    setSelectedConflict(null);
  }

  function toggleSelectOne(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  const allFilteredSelected = filteredList.length > 0 && filteredList.every((c) => selectedIds.has(c.id));

  function toggleSelectAllFiltered() {
    setSelectedIds((prev) => {
      if (allFilteredSelected) {
        const next = new Set(prev);
        filteredList.forEach((c) => next.delete(c.id));
        return next;
      }
      const next = new Set(prev);
      filteredList.forEach((c) => next.add(c.id));
      return next;
    });
  }

  const selectedConflicts = conflicts.filter((c) => selectedIds.has(c.id));

  function handleBulkSuccess(updated: ConflictItem[]) {
    const updatedIds = new Set(updated.map((u) => u.id));
    setConflicts((prev) => prev.map((item) => {
      const match = updated.find((u) => u.id === item.id);
      return match ?? item;
    }));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      updatedIds.forEach((id) => next.delete(id));
      return next;
    });
  }

  return (
    <div style={styles.container}>
      {/* 헤더 타이틀 */}
      <header style={styles.header}>
        <div>
          <div style={styles.breadcrumb}>거버넌스 &gt; 정책 상충 통제</div>
          <h2 style={styles.title}>정책 상충 거버넌스 대시보드</h2>
          <p style={styles.subtext}>
            자연어 추론(NLI) AI가 전사 규정·의사결정 간의 모순 및 상충을 실시간 탐지하고,
            정책 의결 및 대체 관계를 통제하는 중앙 거버넌스 센터입니다.
          </p>
        </div>
        <div style={styles.headerActions}>
          <Button variant="default" size="sm" onClick={loadData} disabled={loading}>
            {loading ? '동기화 중...' : '새로고침'}
          </Button>
        </div>
      </header>

      {/* 통계 KPI 카드 영역 */}
      <div style={styles.kpiGrid}>
        <div style={styles.kpiCard}>
          <span style={styles.kpiLabel}>총 감지된 상충</span>
          <strong style={styles.kpiValue}>{stats.total}</strong>
          <span style={styles.kpiHint}>전체 라이프사이클</span>
        </div>
        <div style={{ ...styles.kpiCard, borderLeft: '4px solid var(--err)' }}>
          <span style={styles.kpiLabel}>조치 필요 (미해결)</span>
          <strong style={{ ...styles.kpiValue, color: 'var(--err)' }}>{stats.open}</strong>
          <span style={styles.kpiHint}>즉시 검토 및 개정 대상</span>
        </div>
        <div style={{ ...styles.kpiCard, borderLeft: '4px solid var(--warn)' }}>
          <span style={styles.kpiLabel}>고위험 상충 (≥ 80%)</span>
          <strong style={{ ...styles.kpiValue, color: 'var(--warn)' }}>{stats.highRisk}</strong>
          <span style={styles.kpiHint}>직접 모순/조직 혼선 유발</span>
        </div>
        <div style={{ ...styles.kpiCard, borderLeft: '4px solid var(--ok)' }}>
          <span style={styles.kpiLabel}>해결 완료</span>
          <strong style={{ ...styles.kpiValue, color: 'var(--ok)' }}>{stats.resolved}</strong>
          <span style={styles.kpiHint}>공식 규정 대체 완료</span>
        </div>
        <div style={{ ...styles.kpiCard, borderLeft: '4px solid var(--ink-3)' }}>
          <span style={styles.kpiLabel}>예외 허용</span>
          <strong style={{ ...styles.kpiValue, color: 'var(--ink-2)' }}>{stats.dismissed}</strong>
          <span style={styles.kpiHint}>특수 상황 인정</span>
        </div>
      </div>

      {/* 필터 및 검색 바 */}
      <div style={styles.filterBar}>
        <div style={styles.statusTabs}>
          <button
            onClick={() => setStatusFilter('ALL')}
            style={tabBtnStyle(statusFilter === 'ALL')}
          >
            전체 ({stats.total})
          </button>
          <button
            onClick={() => setStatusFilter('OPEN')}
            style={tabBtnStyle(statusFilter === 'OPEN')}
          >
            조치 필요 ({stats.open})
          </button>
          <button
            onClick={() => setStatusFilter('RESOLVED')}
            style={tabBtnStyle(statusFilter === 'RESOLVED')}
          >
            해결 완료 ({stats.resolved})
          </button>
          <button
            onClick={() => setStatusFilter('DISMISSED')}
            style={tabBtnStyle(statusFilter === 'DISMISSED')}
          >
            예외 허용 ({stats.dismissed})
          </button>
          <button
            onClick={() => setStatusFilter('SUPERSEDED')}
            style={tabBtnStyle(statusFilter === 'SUPERSEDED')}
          >
            개정으로 만료 ({stats.superseded})
          </button>
        </div>

        <div style={styles.filterControls}>
          <label style={styles.mineToggle}>
            <input
              type="checkbox"
              checked={mineOnly}
              onChange={() => setMineOnly(!mineOnly)}
              style={{ width: 'auto' }}
            />
            내 문서 관련만
          </label>
          <select
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            style={styles.select}
          >
            <option value="ALL">모든 심각도</option>
            <option value="CRITICAL">치명적 (CRITICAL ≥ 85%)</option>
            <option value="HIGH">높음 (HIGH 70%~84%)</option>
            <option value="MEDIUM">보통 (MEDIUM &lt; 70%)</option>
          </select>

          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="문서명, 사유, 부서 검색..."
            style={styles.searchInput}
          />
        </div>
      </div>

      {/* 일괄 처리 도구 모음 */}
      {isAdmin && filteredList.length > 0 && (
        <div style={styles.bulkBar}>
          <label style={styles.bulkSelectAll}>
            <input
              type="checkbox"
              checked={allFilteredSelected}
              onChange={toggleSelectAllFiltered}
              style={{ width: 'auto' }}
            />
            현재 목록 전체 선택 ({filteredList.length}건)
          </label>
          {selectedIds.size > 0 && (
            <div style={styles.bulkActions}>
              <span style={styles.bulkCount}>{selectedIds.size}건 선택됨</span>
              <Button size="sm" onClick={() => setSelectedIds(new Set())}>선택 해제</Button>
              <Button size="sm" variant="primary" onClick={() => setShowBulkModal(true)}>
                일괄 조치
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 에러 메시지 */}
      {error && <div style={styles.errorBox}>{error}</div>}

      {/* 로딩 표시 */}
      {loading && conflicts.length === 0 && (
        <div style={styles.emptyBox}>데이터를 동기화하고 있습니다...</div>
      )}

      {/* 상충 목록 */}
      {!loading && filteredList.length === 0 && (
        <div style={styles.emptyBox}>
          {mineOnly && !searchQuery && statusFilter === 'ALL' && severityFilter === 'ALL'
            ? '내 문서에 걸린 정책 상충이 없습니다. "내 문서 관련만"을 해제하면 전체를 볼 수 있습니다.'
            : searchQuery || statusFilter !== 'ALL' || severityFilter !== 'ALL' || mineOnly
              ? '조건에 부합하는 정책 상충 건이 없습니다.'
              : '현재 감지된 정책 상충이 없습니다. 모든 규정이 상호 일관성을 유지하고 있습니다.'}
        </div>
      )}

      <div style={styles.conflictList}>
        {filteredList.map((c) => (
          <div key={c.id} id={`conflict-${c.id}`} style={conflictCardStyle(c.status, c.id === highlightId)}>
            {/* 카드 상단 바 */}
            <div style={styles.cardHeader}>
              <div style={styles.cardHeaderLeft}>
                {isAdmin && (
                  <input
                    type="checkbox"
                    checked={selectedIds.has(c.id)}
                    onChange={() => toggleSelectOne(c.id)}
                    style={{ width: 'auto' }}
                    aria-label={`상충 #${c.id} 선택`}
                  />
                )}
                <span style={styles.conflictId}>상충 #{c.id}</span>
                {renderSeverityBadge(c.severityScore)}
                <Badge tone="accent">{formatConflictType(c.conflictType)}</Badge>
                {renderStatusBadge(c.status)}
              </div>
              <div style={styles.cardHeaderRight}>
                <span style={styles.dateText}>
                  감지일: {new Date(c.createdAt).toLocaleDateString()}
                </span>
                {isAdmin && (
                  <Button
                    variant={c.status === 'OPEN' ? 'primary' : 'default'}
                    size="sm"
                    onClick={() => setSelectedConflict(c)}
                  >
                    {c.status === 'OPEN' ? '조치하기' : '상태 변경'}
                  </Button>
                )}
              </div>
            </div>

            {/* 신규 vs 기존 정책 대조 뷰 */}
            <div style={styles.comparisonGrid}>
              <div style={styles.docBox}>
                <div style={styles.docBoxHeader}>
                  <span style={styles.docBoxBadgeSource}>신규 등록 문서</span>
                  <button
                    onClick={() => onOpenDocument(c.sourceDocId)}
                    style={styles.docTitleLink}
                    title="문서 상세 보기"
                  >
                    {c.sourceDocTitle} ↗
                  </button>
                </div>
                <div style={styles.clauseBox}>
                  <strong style={styles.clauseLabel}>[상충 조항 발췌]</strong>
                  <p style={styles.clauseText}>{c.sourceClause}</p>
                </div>
              </div>

              <div style={styles.vsDivider}>VS</div>

              <div style={styles.docBox}>
                <div style={styles.docBoxHeader}>
                  <span style={styles.docBoxBadgeTarget}>기존 유효 문서</span>
                  <button
                    onClick={() => onOpenDocument(c.targetDocId)}
                    style={styles.docTitleLink}
                    title="문서 상세 보기"
                  >
                    {c.targetDocTitle} ↗
                  </button>
                </div>
                <div style={styles.clauseBox}>
                  <strong style={styles.clauseLabel}>[기존 조항 발췌]</strong>
                  <p style={styles.clauseText}>{c.targetClause}</p>
                </div>
              </div>
            </div>

            {/* AI 분석 사유 및 영향 부서 */}
            <div style={styles.reasoningBox}>
              <div style={styles.reasoningHead}>
                <span style={styles.aiTag}>AI NLI 심층 분석 추론</span>
                <div style={styles.teamsContainer}>
                  <span style={styles.teamsLabel}>영향 부서:</span>
                  {c.affectedTeams.map((team, idx) => (
                    <span key={idx} style={styles.teamBadge}>
                      {team}
                    </span>
                  ))}
                </div>
              </div>
              <p style={styles.reasoningContent}>{c.reasoning}</p>
            </div>

            {/* 개정으로 자동 만료된 건: 사람이 처리한 게 아니라 처리자·의결 사유가 없다 */}
            {c.status === 'SUPERSEDED' && (
              <div style={styles.resolvedInfoNeutral}>
                <div style={styles.resolvedHead}>
                  <strong>🗂 개정으로 만료 (SUPERSEDED)</strong>
                </div>
                <p style={styles.resolvedNote}>
                  이 상충은 아래 신규 등록 문서가 개정되면서 판정 근거 조항이 바뀌어 자동으로 닫혔습니다.
                  개정된 판을 기준으로 다시 감지된 상충이 있다면 목록에 별도 항목으로 올라옵니다.
                </p>
              </div>
            )}

            {/* 조치 완료 정보 (RESOLVED 또는 DISMISSED인 경우) */}
            {c.status !== 'OPEN' && c.status !== 'SUPERSEDED' && (
              <div
                style={
                  c.status === 'RESOLVED' ? styles.resolvedInfoOk : styles.resolvedInfoNeutral
                }
              >
                <div style={styles.resolvedHead}>
                  <strong>
                    {c.status === 'RESOLVED' ? '✅ 조치 완료 (공식 개정)' : '⚡ 예외 허용 처리'}
                  </strong>
                  <span style={styles.resolvedMeta}>
                    처리자: {c.resolvedBy ?? '관리자'} | 처리일시:{' '}
                    {c.resolvedAt ? new Date(c.resolvedAt).toLocaleString() : '-'}
                  </span>
                </div>
                <p style={styles.resolvedNote}>
                  <strong>의결 사유:</strong> {c.resolutionNote}
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 조치 모달 */}
      {selectedConflict && (
        <ConflictActionModal
          conflict={selectedConflict}
          onClose={() => setSelectedConflict(null)}
          onSuccess={handleActionSuccess}
        />
      )}

      {/* 일괄 조치 모달 */}
      {showBulkModal && selectedConflicts.length > 0 && (
        <BulkConflictActionModal
          conflicts={selectedConflicts}
          onClose={() => setShowBulkModal(false)}
          onSuccess={(updated) => {
            handleBulkSuccess(updated);
            if (updated.length === selectedConflicts.length) setShowBulkModal(false);
          }}
        />
      )}
    </div>
  );
}

function renderSeverityBadge(score: number) {
  const percent = Math.round(score * 100);
  if (score >= SEVERITY_HIGH) return <Badge tone="err">치명적 ({percent}%)</Badge>;
  if (score >= SEVERITY_MEDIUM) return <Badge tone="warn">높음 ({percent}%)</Badge>;
  return <Badge tone="neutral">보통 ({percent}%)</Badge>;
}

function renderStatusBadge(status: ConflictStatus) {
  if (status === 'OPEN') return <Badge tone="warn">조치 필요</Badge>;
  if (status === 'RESOLVED') return <Badge tone="ok">해결 완료</Badge>;
  if (status === 'SUPERSEDED') return <Badge tone="neutral">개정으로 만료</Badge>;
  return <Badge tone="neutral">예외 허용</Badge>;
}

function formatConflictType(type: string): string {
  switch (type) {
    case 'DIRECT_CONTRADICTION':
      return '직접 모순';
    case 'SCOPE_MISMATCH':
      return '적용 범위 불일치';
    case 'CONDITIONAL_CONFLICT':
      return '조건부 충돌';
    default:
      return type;
  }
}

function tabBtnStyle(active: boolean): React.CSSProperties {
  return {
    padding: '7px 14px',
    fontSize: 13,
    fontWeight: 700,
    background: active ? 'var(--ink)' : 'transparent',
    color: active ? '#fff' : 'var(--ink-2)',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  };
}

function conflictCardStyle(status: ConflictStatus, highlighted = false): React.CSSProperties {
  const borderColor =
    status === 'OPEN' ? '#f0d8bf' : status === 'RESOLVED' ? '#bfe3d6' : 'var(--rule)';
  return {
    background: 'var(--paper)',
    borderRadius: 10,
    // 알림 링크로 들어온 건은 테두리를 강조해 어느 건인지 바로 보이게 한다
    border: `1.5px solid ${highlighted ? 'var(--accent)' : borderColor}`,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    boxShadow: highlighted
      ? '0 0 0 3px var(--accent-bg), 0 2px 8px rgba(0, 0, 0, 0.08)'
      : '0 2px 8px rgba(0, 0, 0, 0.04)',
  };
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 24,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 20,
  },
  breadcrumb: {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--ink-3)',
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  title: {
    fontSize: 22,
    fontWeight: 800,
    margin: 0,
    color: 'var(--ink)',
  },
  subtext: {
    fontSize: 13,
    color: 'var(--ink-3)',
    margin: '6px 0 0',
    lineHeight: 1.5,
    maxWidth: 800,
  },
  headerActions: {
    display: 'flex',
    gap: 8,
  },
  kpiGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
    gap: 12,
  },
  kpiCard: {
    background: 'var(--paper)',
    borderRadius: 8,
    border: '1px solid var(--rule)',
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    boxShadow: '0 1px 4px rgba(0, 0, 0, 0.03)',
  },
  kpiLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--ink-3)',
  },
  kpiValue: {
    fontSize: 24,
    fontWeight: 800,
    color: 'var(--ink)',
  },
  kpiHint: {
    fontSize: 11,
    color: 'var(--ink-3)',
  },
  filterBar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 16,
    flexWrap: 'wrap',
    padding: '10px 14px',
    background: 'var(--tint)',
    borderRadius: 8,
    border: '1px solid var(--rule)',
  },
  statusTabs: {
    display: 'flex',
    gap: 4,
  },
  filterControls: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
  },
  mineToggle: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 12.5,
    color: 'var(--ink-2)',
    whiteSpace: 'nowrap',
  },
  select: {
    padding: '7px 10px',
    borderRadius: 6,
    border: '1px solid var(--rule)',
    background: 'var(--paper)',
    color: 'var(--ink)',
    fontSize: 12.5,
    outline: 'none',
  },
  searchInput: {
    padding: '7px 12px',
    borderRadius: 6,
    border: '1px solid var(--rule)',
    background: 'var(--paper)',
    color: 'var(--ink)',
    fontSize: 12.5,
    outline: 'none',
    width: 200,
  },
  bulkBar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 12,
    flexWrap: 'wrap',
    padding: '8px 14px',
    background: 'var(--paper)',
    borderRadius: 8,
    border: '1px solid var(--rule)',
  },
  bulkSelectAll: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12.5,
    color: 'var(--ink-2)',
    cursor: 'pointer',
  },
  bulkActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  bulkCount: {
    fontSize: 12.5,
    fontWeight: 700,
    color: 'var(--accent)',
  },
  errorBox: {
    padding: '12px 16px',
    borderRadius: 8,
    background: 'var(--err-bg)',
    color: 'var(--err)',
    border: '1px solid #f5c9c9',
    fontSize: 13,
    fontWeight: 600,
  },
  emptyBox: {
    padding: '48px 24px',
    textAlign: 'center',
    borderRadius: 8,
    border: '1px dashed var(--rule)',
    color: 'var(--ink-3)',
    fontSize: 13,
  },
  conflictList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 10,
    paddingBottom: 12,
    borderBottom: '1px solid var(--rule)',
  },
  cardHeaderLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  conflictId: {
    fontSize: 13,
    fontWeight: 800,
    color: 'var(--ink)',
  },
  cardHeaderRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  dateText: {
    fontSize: 12,
    color: 'var(--ink-3)',
  },
  comparisonGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr auto 1fr',
    gap: 12,
    alignItems: 'stretch',
  },
  docBox: {
    padding: '12px 14px',
    borderRadius: 8,
    background: 'var(--tint)',
    border: '1px solid var(--rule)',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  docBoxHeader: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  docBoxBadgeSource: {
    fontSize: 10,
    fontWeight: 700,
    color: 'var(--accent)',
    letterSpacing: '0.05em',
  },
  docBoxBadgeTarget: {
    fontSize: 10,
    fontWeight: 700,
    color: 'var(--warn)',
    letterSpacing: '0.05em',
  },
  docTitleLink: {
    background: 'transparent',
    border: 'none',
    textAlign: 'left',
    padding: 0,
    fontSize: 13.5,
    fontWeight: 700,
    color: 'var(--ink)',
    cursor: 'pointer',
    lineHeight: 1.4,
    textDecoration: 'underline',
  },
  clauseBox: {
    marginTop: 'auto',
    padding: '8px 10px',
    background: 'var(--paper)',
    borderRadius: 6,
    border: '1px solid var(--rule)',
    fontSize: 12,
  },
  clauseLabel: {
    display: 'block',
    fontSize: 11,
    color: 'var(--ink-3)',
    marginBottom: 4,
  },
  clauseText: {
    margin: 0,
    lineHeight: 1.5,
    color: 'var(--ink)',
    whiteSpace: 'pre-wrap',
  },
  vsDivider: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    fontWeight: 800,
    color: 'var(--ink-3)',
    padding: '0 4px',
  },
  reasoningBox: {
    padding: '12px 14px',
    borderRadius: 8,
    background: 'rgba(239, 68, 68, 0.04)',
    border: '1px solid rgba(239, 68, 68, 0.15)',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  reasoningHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 8,
  },
  aiTag: {
    fontSize: 11,
    fontWeight: 700,
    color: 'var(--err)',
  },
  teamsContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  teamsLabel: {
    fontSize: 11,
    color: 'var(--ink-3)',
  },
  teamBadge: {
    fontSize: 11,
    fontWeight: 600,
    padding: '1px 6px',
    borderRadius: 4,
    background: 'var(--paper)',
    border: '1px solid var(--rule)',
    color: 'var(--ink-2)',
  },
  reasoningContent: {
    margin: 0,
    fontSize: 12.5,
    color: 'var(--ink)',
    lineHeight: 1.5,
  },
  resolvedInfoOk: {
    padding: '12px 14px',
    borderRadius: 8,
    background: 'var(--ok-bg)',
    border: '1px solid #bfe3d6',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  resolvedInfoNeutral: {
    padding: '12px 14px',
    borderRadius: 8,
    background: 'var(--tint)',
    border: '1px solid var(--rule)',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  resolvedHead: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontSize: 12,
  },
  resolvedMeta: {
    fontSize: 11,
    color: 'var(--ink-3)',
  },
  resolvedNote: {
    margin: 0,
    fontSize: 12,
    color: 'var(--ink)',
    lineHeight: 1.4,
  },
};
