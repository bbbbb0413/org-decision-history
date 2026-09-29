import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TeamNotificationSettingsPanel } from './TeamNotificationSettingsPanel';
import { api } from '../../api/client';
import type { CurrentUser } from '../../api/types';

const TEAM = { id: 1, organizationId: 1, name: '결제팀' };

function user(roles: string[], teams = [TEAM]): CurrentUser {
  return {
    id: 1, email: 'a@b.com', name: '테스터', status: 'ACTIVE',
    organizationId: 1, organizationName: '플랫폼본부', teams, roles,
  };
}

const EMPTY_CHANNEL = {
  teamId: 1, slackWebhookUrl: null, teamsWebhookUrl: null, notificationEmails: [], updatedAt: null,
};

describe('TeamNotificationSettingsPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('시스템 관리자에게는 조직 선택이 보이고, 조직 목록을 불러온다', async () => {
    const organizations = vi.spyOn(api, 'organizations').mockResolvedValue([{ id: 1, name: '플랫폼본부' }]);
    render(<TeamNotificationSettingsPanel user={user(['SYSTEM_ADMIN'], [])} />);

    await waitFor(() => expect(organizations).toHaveBeenCalled());
    expect(screen.getByText('조직')).toBeInTheDocument();
  });

  it('팀 관리자에게는 조직 선택이 없고 자기 소속 팀만 후보로 나온다', async () => {
    // 팀 관리자는 조직 전체를 볼 권한이 없다 — 조직 목록을 부르면 서버가 403 을 준다
    const organizations = vi.spyOn(api, 'organizations');
    const teamsOf = vi.spyOn(api, 'teamsOf');
    render(<TeamNotificationSettingsPanel user={user(['TEAM_ADMIN'])} />);

    expect(screen.queryByText('조직')).not.toBeInTheDocument();
    expect(organizations).not.toHaveBeenCalled();
    expect(teamsOf).not.toHaveBeenCalled();
    expect(screen.getByRole('option', { name: '결제팀' })).toBeInTheDocument();
  });

  it('조직 관리자는 자기 조직의 팀 목록을 불러온다', async () => {
    const teamsOf = vi.spyOn(api, 'teamsOf').mockResolvedValue([TEAM]);
    render(<TeamNotificationSettingsPanel user={user(['ORG_ADMIN'], [])} />);

    await waitFor(() => expect(teamsOf).toHaveBeenCalledWith(1));
  });

  it('팀을 고르면 설정을 불러오고, 저장하면 입력값을 그대로 보낸다', async () => {
    vi.spyOn(api, 'teamNotificationChannel').mockResolvedValue(EMPTY_CHANNEL);
    const update = vi.spyOn(api, 'updateTeamNotificationChannel')
      .mockResolvedValue({ ...EMPTY_CHANNEL, slackWebhookUrl: 'https://hooks.slack.test/x', updatedAt: '2026-09-18T00:00:00Z' });
    render(<TeamNotificationSettingsPanel user={user(['TEAM_ADMIN'])} />);

    fireEvent.change(screen.getByRole('combobox'), { target: { value: '1' } });
    await screen.findByPlaceholderText(/hooks.slack.com/);

    fireEvent.change(screen.getByPlaceholderText(/hooks.slack.com/), {
      target: { value: 'https://hooks.slack.test/x' },
    });
    fireEvent.change(screen.getByPlaceholderText(/팀원 이메일로 자동 발송/), {
      target: { value: 'a@t.com, b@t.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));

    await waitFor(() => {
      // 이메일은 콤마로 끊어 배열로 보내고, 비운 칸은 null 로 보낸다
      expect(update).toHaveBeenCalledWith(1, {
        slackWebhookUrl: 'https://hooks.slack.test/x',
        teamsWebhookUrl: null,
        notificationEmails: ['a@t.com', 'b@t.com'],
      });
    });
    expect(await screen.findByText('저장했습니다.')).toBeInTheDocument();
  });
});
