import { useCallback, useEffect, useState } from 'react';
import { api, HttpError } from '../../api/client';
import type { CurrentUser, Organization, Team, TeamNotificationChannel } from '../../api/types';
import { Button } from '../ui/Button';
import { Field } from '../ui/Field';

interface Props {
  user: CurrentUser;
}

function emailsToText(emails: string[]): string {
  return emails.join(', ');
}

function textToEmails(text: string): string[] {
  return text.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
}

/**
 * 팀별 거버넌스 알림 채널(Slack/Teams 웹훅, 알림 이메일) 설정.
 * 미설정 팀은 governance-worker 가 전역 설정 채널로 폴백한다 — 여기서 아무것도
 * 저장하지 않아도 기존 알림 동작은 그대로 유지된다.
 */
export function TeamNotificationSettingsPanel({ user }: Props) {
  const isSystemAdmin = user.roles.includes('SYSTEM_ADMIN');
  const isOrgAdmin = user.roles.includes('ORG_ADMIN');

  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [orgId, setOrgId] = useState<number | ''>(isSystemAdmin ? '' : (user.organizationId ?? ''));
  const [teamOptions, setTeamOptions] = useState<Team[]>(isSystemAdmin ? [] : user.teams);
  const [teamId, setTeamId] = useState<number | ''>('');

  const [channel, setChannel] = useState<TeamNotificationChannel | null>(null);
  const [slackUrl, setSlackUrl] = useState('');
  const [teamsUrl, setTeamsUrl] = useState('');
  const [emailsText, setEmailsText] = useState('');

  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (isSystemAdmin) api.organizations().then(setOrgs).catch(() => setOrgs([]));
  }, [isSystemAdmin]);

  useEffect(() => {
    if (isOrgAdmin && user.organizationId) {
      api.teamsOf(user.organizationId).then(setTeamOptions).catch(() => setTeamOptions([]));
    }
  }, [isOrgAdmin, user.organizationId]);

  const loadChannel = useCallback(async (id: number) => {
    setLoadError(null);
    setSaved(false);
    try {
      const result = await api.teamNotificationChannel(id);
      setChannel(result);
      setSlackUrl(result.slackWebhookUrl ?? '');
      setTeamsUrl(result.teamsWebhookUrl ?? '');
      setEmailsText(emailsToText(result.notificationEmails));
    } catch (e) {
      setChannel(null);
      setLoadError(e instanceof HttpError ? e.body.message : '설정을 불러오지 못했습니다.');
    }
  }, []);

  function pickOrg(id: number) {
    setOrgId(id);
    setTeamId('');
    setChannel(null);
    api.teamsOf(id).then(setTeamOptions).catch(() => setTeamOptions([]));
  }

  function pickTeam(id: number) {
    setTeamId(id);
    void loadChannel(id);
  }

  async function save() {
    if (teamId === '') return;
    setBusy(true);
    setSaveError(null);
    setSaved(false);
    try {
      const result = await api.updateTeamNotificationChannel(teamId, {
        slackWebhookUrl: slackUrl.trim() || null,
        teamsWebhookUrl: teamsUrl.trim() || null,
        notificationEmails: textToEmails(emailsText),
      });
      setChannel(result);
      setSaved(true);
    } catch (e) {
      setSaveError(e instanceof HttpError ? e.body.message : '저장하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={styles.card}>
      <header style={styles.head}>
        <div>
          <div style={styles.title}>팀별 알림 채널</div>
          <div style={styles.hint}>
            팀에 상충 알림을 받을 Slack/Teams 웹훅이나 이메일을 지정한다. 비워두면 전역 설정 채널로 발송된다.
          </div>
        </div>
      </header>

      <div style={styles.body}>
        <div style={styles.editGrid}>
          {isSystemAdmin && (
            <Field label="조직">
              <select value={orgId} style={styles.select} onChange={(e) => pickOrg(Number(e.target.value))}>
                <option value="" disabled>선택</option>
                {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="팀">
            <select
              value={teamId}
              style={styles.select}
              disabled={teamOptions.length === 0}
              onChange={(e) => pickTeam(Number(e.target.value))}
            >
              <option value="" disabled>선택</option>
              {teamOptions.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
        </div>

        {loadError && <div style={styles.error}>{loadError}</div>}

        {teamId !== '' && channel && (
          <>
            <div style={styles.editGrid}>
              <Field label="Slack 웹훅 URL">
                <input value={slackUrl} onChange={(e) => setSlackUrl(e.target.value)}
                       placeholder="https://hooks.slack.com/services/…" style={{ width: '100%' }} />
              </Field>
              <Field label="Teams 웹훅 URL">
                <input value={teamsUrl} onChange={(e) => setTeamsUrl(e.target.value)}
                       placeholder="https://outlook.office.com/webhook/…" style={{ width: '100%' }} />
              </Field>
            </div>
            <div style={styles.editGrid}>
              <Field label="알림 이메일 (콤마 구분)">
                <input value={emailsText} onChange={(e) => setEmailsText(e.target.value)}
                       placeholder="비워두면 팀원 이메일로 자동 발송" style={{ width: '100%' }} />
              </Field>
            </div>

            {saveError && <div style={styles.error}>{saveError}</div>}
            {saved && <div style={styles.ok}>저장했습니다.</div>}
            {channel.updatedAt && <div style={styles.hint}>마지막 수정: {new Date(channel.updatedAt).toLocaleString()}</div>}

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button variant="primary" disabled={busy} onClick={save}>
                {busy ? '저장 중…' : '저장'}
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden', marginTop: 16 },
  head: { padding: 16, borderBottom: '1px solid var(--rule)' },
  title: { fontSize: 13, fontWeight: 700 },
  body: { padding: 16, display: 'flex', flexDirection: 'column', gap: 12 },
  editGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 16px' },
  select: { width: '100%', padding: '8px 10px', border: '1px solid var(--rule)', borderRadius: 6, background: 'var(--paper)', font: 'inherit' },
  hint: { fontSize: 12, color: 'var(--ink-3)', marginTop: 4 },
  error: { padding: '10px 12px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 13 },
  ok: { padding: '10px 12px', background: 'var(--accent-bg)', borderRadius: 6, fontSize: 13 },
};
