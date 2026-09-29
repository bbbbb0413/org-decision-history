import { Fragment, useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import {
  ROLE_LABEL,
  type CurrentUser, type IssuedTemporaryPassword, type IssuedUserAccount, type Organization, type Paged,
  type SystemRole, type Team, type UserSummary,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Field } from '../ui/Field';

interface Props {
  user: CurrentUser;
}

const ALL_ROLES: SystemRole[] = ['SYSTEM_ADMIN', 'ORG_ADMIN', 'TEAM_ADMIN', 'MEMBER'];

/** 관리자 자신의 역할이 허용하는 부여 가능 역할 상한. 서버가 최종 판정하며 이건 화면 안내용이다. */
function roleCeiling(actor: CurrentUser): SystemRole[] {
  if (actor.roles.includes('SYSTEM_ADMIN')) return ALL_ROLES;
  if (actor.roles.includes('ORG_ADMIN')) return ['ORG_ADMIN', 'TEAM_ADMIN', 'MEMBER'];
  return ['TEAM_ADMIN', 'MEMBER'];
}

/** 사용자 소속·역할 관리. 관리 범위는 서버가 판정하고 화면은 안내만 한다. */
export function UserManagementPanel({ user }: Props) {
  const isSystemAdmin = user.roles.includes('SYSTEM_ADMIN');
  const isOrgAdmin = user.roles.includes('ORG_ADMIN');
  const ceiling = roleCeiling(user);

  const [data, setData] = useState<Paged<UserSummary> | null>(null);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [orgs, setOrgs] = useState<Organization[]>([]);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editOrgId, setEditOrgId] = useState<number | ''>('');
  const [editTeamOptions, setEditTeamOptions] = useState<Team[]>([]);
  const [editTeamIds, setEditTeamIds] = useState<number[]>([]);
  const [editRoles, setEditRoles] = useState<SystemRole[]>([]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 계정 비활성화·임시 비밀번호 재발급
  const [accountBusyId, setAccountBusyId] = useState<number | null>(null);
  const [reissued, setReissued] = useState<IssuedTemporaryPassword | null>(null);

  // 조직 생성 — 시스템 관리자만
  const [showOrgForm, setShowOrgForm] = useState(false);
  const [newOrgName, setNewOrgName] = useState('');
  const [orgError, setOrgError] = useState<string | null>(null);
  const [orgBusy, setOrgBusy] = useState(false);

  // 계정 발급
  const [showIssueForm, setShowIssueForm] = useState(false);
  const [issueEmail, setIssueEmail] = useState('');
  const [issueName, setIssueName] = useState('');
  const [issueOrgId, setIssueOrgId] = useState<number | ''>(isSystemAdmin ? '' : (user.organizationId ?? ''));
  const [issueTeamOptions, setIssueTeamOptions] = useState<Team[]>(isSystemAdmin ? [] : user.teams);
  const [issueTeamIds, setIssueTeamIds] = useState<number[]>([]);
  const [issueRoles, setIssueRoles] = useState<SystemRole[]>([]);
  const [issueError, setIssueError] = useState<string | null>(null);
  const [issueBusy, setIssueBusy] = useState(false);
  const [issued, setIssued] = useState<IssuedUserAccount | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.users({ q, page, size: 20 }));
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '목록을 불러오지 못했습니다.');
    }
  }, [q, page]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (isSystemAdmin) api.organizations().then(setOrgs).catch(() => setOrgs([])); }, [isSystemAdmin]);

  function startEdit(target: UserSummary) {
    setEditingId(target.id);
    setSaveError(null);
    setEditOrgId(target.organizationId ?? '');
    setEditRoles(target.roles);
    if (target.organizationId && (isSystemAdmin || isOrgAdmin)) {
      api.teamsOf(target.organizationId).then(setEditTeamOptions).catch(() => setEditTeamOptions([]));
    } else {
      // 팀 관리자는 자기 팀 범위 밖은 서버가 그대로 유지하므로, 자기 팀만 후보로 보여준다.
      setEditTeamOptions(user.teams);
    }
    setEditTeamIds(target.teams.map((t) => t.id));
  }

  function cancelEdit() {
    setEditingId(null);
    setSaveError(null);
  }

  function toggleTeam(id: number) {
    setEditTeamIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  function toggleRole(role: SystemRole) {
    setEditRoles((rs) => (rs.includes(role) ? rs.filter((r) => r !== role) : [...rs, role]));
  }

  async function createOrganization() {
    if (!newOrgName.trim()) {
      setOrgError('조직명을 입력해 주세요.');
      return;
    }
    setOrgBusy(true);
    setOrgError(null);
    try {
      await api.createOrganization(newOrgName.trim());
      setNewOrgName('');
      setShowOrgForm(false);
      setOrgs(await api.organizations());
    } catch (e) {
      setOrgError(e instanceof HttpError ? e.body.message : '조직을 만들지 못했습니다.');
    } finally {
      setOrgBusy(false);
    }
  }

  function pickIssueOrg(orgId: number) {
    setIssueOrgId(orgId);
    setIssueTeamIds([]);
    api.teamsOf(orgId).then(setIssueTeamOptions).catch(() => setIssueTeamOptions([]));
  }

  function toggleIssueTeam(id: number) {
    setIssueTeamIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  function toggleIssueRole(role: SystemRole) {
    setIssueRoles((rs) => (rs.includes(role) ? rs.filter((r) => r !== role) : [...rs, role]));
  }

  function closeIssueForm() {
    setShowIssueForm(false);
    setIssued(null);
    setIssueError(null);
    setIssueEmail('');
    setIssueName('');
    setIssueTeamIds([]);
    setIssueRoles([]);
  }

  async function submitIssue() {
    if (!issueEmail.trim() || !issueName.trim() || issueOrgId === '' || issueRoles.length === 0) {
      setIssueError('이메일·이름·조직·역할은 필수입니다.');
      return;
    }
    setIssueBusy(true);
    setIssueError(null);
    try {
      const account = await api.issueUserAccount({
        email: issueEmail.trim(), name: issueName.trim(),
        organizationId: issueOrgId, teamIds: issueTeamIds, roles: issueRoles,
      });
      setIssued(account);
      await load();
    } catch (e) {
      setIssueError(e instanceof HttpError ? e.body.message : '계정을 발급하지 못했습니다.');
    } finally {
      setIssueBusy(false);
    }
  }

  async function toggleStatus(target: UserSummary) {
    const next = target.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE';
    if (next === 'DISABLED'
        && !window.confirm(`${target.name} 계정을 비활성화하면 즉시 로그아웃되고 다시 로그인할 수 없습니다. 진행할까요?`)) {
      return;
    }
    setAccountBusyId(target.id);
    setSaveError(null);
    try {
      await api.updateUserStatus(target.id, next);
      await load();
    } catch (e) {
      setSaveError(e instanceof HttpError ? e.body.message : '계정 상태를 바꾸지 못했습니다.');
    } finally {
      setAccountBusyId(null);
    }
  }

  async function resetPassword(target: UserSummary) {
    if (!window.confirm(`${target.name} 계정의 비밀번호를 임시 비밀번호로 재발급합니다. 기존 비밀번호와 세션은 즉시 무효가 됩니다. 진행할까요?`)) {
      return;
    }
    setAccountBusyId(target.id);
    setSaveError(null);
    try {
      setReissued(await api.resetUserPassword(target.id));
    } catch (e) {
      setSaveError(e instanceof HttpError ? e.body.message : '임시 비밀번호를 재발급하지 못했습니다.');
    } finally {
      setAccountBusyId(null);
    }
  }

  async function save(targetId: number) {
    if (editOrgId === '' || editRoles.length === 0) {
      setSaveError('조직과 역할은 최소 하나 이상 있어야 합니다.');
      return;
    }
    setBusy(true);
    setSaveError(null);
    try {
      await api.updateUserMembership(targetId, editOrgId, editTeamIds);
      await api.updateUserRoles(targetId, editRoles);
      setEditingId(null);
      await load();
    } catch (e) {
      setSaveError(e instanceof HttpError ? e.body.message : '저장하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <input
          value={q}
          placeholder="이름 · 이메일로 검색"
          onChange={(e) => { setQ(e.target.value); setPage(0); }}
          style={{ flex: 1 }}
        />
        {isSystemAdmin && (
          <Button size="sm" onClick={() => { setShowOrgForm((v) => !v); setOrgError(null); }}>
            {showOrgForm ? '취소' : '새 조직'}
          </Button>
        )}
        <Button size="sm" onClick={() => (showIssueForm ? closeIssueForm() : setShowIssueForm(true))}>
          {showIssueForm ? '취소' : '계정 발급'}
        </Button>
        <Button size="sm" onClick={load}>새로 고침</Button>
      </header>

      {showOrgForm && (
        <div style={styles.formPanel}>
          {orgError && <div style={styles.error}>{orgError}</div>}
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={newOrgName}
              placeholder="조직명"
              onChange={(e) => setNewOrgName(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button variant="primary" disabled={orgBusy} onClick={createOrganization}>
              {orgBusy ? '생성 중…' : '생성'}
            </Button>
          </div>
        </div>
      )}

      {showIssueForm && (
        <div style={styles.formPanel}>
          {issued ? (
            <div>
              <div style={styles.issuedNotice}>
                <b>{issued.email}</b> 계정을 발급했습니다. 임시 비밀번호는 지금만 보이며 다시 확인할 수 없습니다.
              </div>
              <div style={styles.tempPassword}>{issued.temporaryPassword}</div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                <Button onClick={closeIssueForm}>닫기</Button>
              </div>
            </div>
          ) : (
            <>
              {issueError && <div style={styles.error}>{issueError}</div>}
              <div style={styles.editGrid}>
                <Field label="이메일">
                  <input value={issueEmail} onChange={(e) => setIssueEmail(e.target.value)}
                         placeholder="user@company.com" style={{ width: '100%' }} />
                </Field>
                <Field label="이름">
                  <input value={issueName} onChange={(e) => setIssueName(e.target.value)}
                         placeholder="이름" style={{ width: '100%' }} />
                </Field>
                <Field label="조직">
                  {isSystemAdmin ? (
                    <select value={issueOrgId} style={styles.select}
                            onChange={(e) => pickIssueOrg(Number(e.target.value))}>
                      <option value="" disabled>선택</option>
                      {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select>
                  ) : (
                    <div style={styles.readonly}>{user.organizationName ?? '미배정'}</div>
                  )}
                </Field>
              </div>
              <div style={styles.editGrid}>
                <Field label="팀">
                  <div style={styles.checkGroup}>
                    {issueTeamOptions.length === 0 && <span style={styles.hint}>선택 가능한 팀이 없습니다.</span>}
                    {issueTeamOptions.map((t) => (
                      <label key={t.id} style={styles.check}>
                        <input type="checkbox" checked={issueTeamIds.includes(t.id)}
                               onChange={() => toggleIssueTeam(t.id)} style={{ width: 'auto', marginRight: 6 }} />
                        {t.name}
                      </label>
                    ))}
                  </div>
                </Field>
                <Field label="역할">
                  <div style={styles.checkGroup}>
                    {ceiling.map((r) => (
                      <label key={r} style={styles.check}>
                        <input type="checkbox" checked={issueRoles.includes(r)}
                               onChange={() => toggleIssueRole(r)} style={{ width: 'auto', marginRight: 6 }} />
                        {ROLE_LABEL[r]}
                      </label>
                    ))}
                  </div>
                </Field>
              </div>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <Button onClick={closeIssueForm}>취소</Button>
                <Button variant="primary" disabled={issueBusy} onClick={submitIssue}>
                  {issueBusy ? '발급 중…' : '발급'}
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {reissued && (
        <div style={styles.formPanel}>
          <div style={styles.issuedNotice}>
            <b>{reissued.email}</b> 계정에 임시 비밀번호를 재발급했습니다. 지금만 보이며 다시 확인할 수 없습니다.
          </div>
          <div style={styles.tempPassword}>{reissued.temporaryPassword}</div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
            <Button onClick={() => setReissued(null)}>닫기</Button>
          </div>
        </div>
      )}

      {error && <div style={styles.error}>{error}</div>}

      {data && data.content.length === 0 && !error && (
        <div style={styles.empty}>관리 범위 안에 사용자가 없습니다.</div>
      )}

      {data && data.content.length > 0 && (
        <>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={styles.th}>이름</th>
                <th style={styles.th}>이메일</th>
                <th style={styles.th}>조직</th>
                <th style={styles.th}>팀</th>
                <th style={styles.th}>역할</th>
                <th style={styles.th}>상태</th>
                <th style={styles.th} />
              </tr>
            </thead>
            <tbody>
              {data.content.map((u) => (
                <Fragment key={u.id}>
                  <tr>
                    <td style={styles.td}><b>{u.name}</b></td>
                    <td style={styles.td}>{u.email}</td>
                    <td style={styles.td}>{u.organizationName ?? '미배정'}</td>
                    <td style={styles.td}>
                      {u.teams.length === 0 ? <span style={{ color: 'var(--ink-3)' }}>—</span>
                        : u.teams.map((t) => t.name).join(', ')}
                    </td>
                    <td style={styles.td}>
                      {u.roles.length === 0 ? <span style={{ color: 'var(--ink-3)' }}>—</span>
                        : u.roles.map((r) => (
                          <Badge key={r} tone={r === 'MEMBER' ? 'neutral' : 'accent'}>{ROLE_LABEL[r]}</Badge>
                        ))}
                    </td>
                    <td style={styles.td}>
                      <Badge tone={u.status === 'ACTIVE' ? 'ok' : 'err'}>
                        {u.status === 'ACTIVE' ? '활성' : '비활성'}
                      </Badge>
                    </td>
                    <td style={{ ...styles.td, textAlign: 'right' }}>
                      {editingId === u.id
                        ? <Button size="sm" onClick={cancelEdit}>닫기</Button>
                        : <Button size="sm" onClick={() => startEdit(u)}>편집</Button>}
                    </td>
                  </tr>
                  {editingId === u.id && (
                    <tr>
                      <td colSpan={7} style={styles.editCell}>
                        {saveError && <div style={styles.error}>{saveError}</div>}
                        <div style={styles.editGrid}>
                          <Field label="조직">
                            {isSystemAdmin ? (
                              <select value={editOrgId} style={styles.select}
                                      onChange={(e) => {
                                        const orgId = Number(e.target.value);
                                        setEditOrgId(orgId);
                                        setEditTeamIds([]);
                                        api.teamsOf(orgId).then(setEditTeamOptions).catch(() => setEditTeamOptions([]));
                                      }}>
                                {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                              </select>
                            ) : (
                              <div style={styles.readonly}>{u.organizationName ?? '미배정'}</div>
                            )}
                          </Field>

                          <Field label="팀">
                            <div style={styles.checkGroup}>
                              {editTeamOptions.length === 0 && <span style={styles.hint}>선택 가능한 팀이 없습니다.</span>}
                              {editTeamOptions.map((t) => (
                                <label key={t.id} style={styles.check}>
                                  <input type="checkbox" checked={editTeamIds.includes(t.id)}
                                         onChange={() => toggleTeam(t.id)} style={{ width: 'auto', marginRight: 6 }} />
                                  {t.name}
                                </label>
                              ))}
                            </div>
                          </Field>

                          <Field label="역할">
                            <div style={styles.checkGroup}>
                              {ceiling.map((r) => (
                                <label key={r} style={styles.check}>
                                  <input type="checkbox" checked={editRoles.includes(r)}
                                         onChange={() => toggleRole(r)} style={{ width: 'auto', marginRight: 6 }} />
                                  {ROLE_LABEL[r]}
                                </label>
                              ))}
                            </div>
                          </Field>
                        </div>

                        {/* 계정 보안 조작은 소속·역할 저장과 별개로 즉시 반영된다 */}
                        <div style={styles.accountActions}>
                          <span style={styles.hint}>
                            계정 보안 — 비활성화·재발급은 저장 버튼과 무관하게 바로 적용되고, 대상의 세션이 즉시 끊깁니다.
                          </span>
                          <div style={{ display: 'flex', gap: 8 }}>
                            <Button size="sm" disabled={accountBusyId === u.id} onClick={() => resetPassword(u)}>
                              임시 비밀번호 재발급
                            </Button>
                            <Button
                              size="sm"
                              variant={u.status === 'ACTIVE' ? 'danger' : 'default'}
                              disabled={accountBusyId === u.id}
                              onClick={() => toggleStatus(u)}
                            >
                              {u.status === 'ACTIVE' ? '계정 비활성화' : '계정 활성화'}
                            </Button>
                          </div>
                        </div>

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                          <Button onClick={cancelEdit}>취소</Button>
                          <Button variant="primary" disabled={busy} onClick={() => save(u.id)}>
                            {busy ? '저장 중…' : '저장'}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
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
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', gap: 10, padding: 16, borderBottom: '1px solid var(--rule)' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', fontSize: 11, fontWeight: 700, color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--rule)', whiteSpace: 'nowrap' },
  td: { padding: '10px 14px', borderBottom: '1px solid var(--rule-soft)', verticalAlign: 'middle' },
  editCell: { padding: '16px 14px', background: '#fbfcfd', borderBottom: '1px solid var(--rule)' },
  editGrid: { display: 'grid', gridTemplateColumns: '1fr 1.4fr 1.4fr', gap: '0 16px', marginBottom: 14 },
  select: { width: '100%', padding: '8px 10px', border: '1px solid var(--rule)', borderRadius: 6, background: 'var(--paper)', font: 'inherit' },
  readonly: { padding: '8px 0', color: 'var(--ink-2)', fontSize: 13 },
  checkGroup: { display: 'flex', flexWrap: 'wrap', gap: '4px 14px' },
  check: { display: 'flex', alignItems: 'center', fontSize: 12.5, cursor: 'pointer' },
  hint: { fontSize: 12, color: 'var(--ink-3)' },
  accountActions: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
    flexWrap: 'wrap', padding: '10px 0 14px', marginBottom: 10,
    borderBottom: '1px dashed var(--rule)',
  },
  foot: { display: 'flex', alignItems: 'center', gap: 12, padding: 14, justifyContent: 'center' },
  pageInfo: { fontSize: 12, color: 'var(--ink-3)' },
  error: { margin: 14, padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  empty: { padding: 32, textAlign: 'center', color: 'var(--ink-3)' },
  formPanel: { padding: 16, borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  issuedNotice: { fontSize: 13, marginBottom: 10 },
  tempPassword: {
    fontFamily: 'var(--mono)', fontSize: 16, fontWeight: 700, letterSpacing: '.04em',
    padding: '10px 14px', background: 'var(--accent-bg)', borderRadius: 6, textAlign: 'center',
  },
};
