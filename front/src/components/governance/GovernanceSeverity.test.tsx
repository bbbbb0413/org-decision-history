import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GovernanceDashboard } from './GovernanceDashboard';
import { api } from '../../api/client';
import type { ConflictItem, CurrentUser } from '../../api/types';

/**
 * 심각도 등급은 서버가 준 `severityLevel` 을 그대로 쓴다.
 *
 * 화면이 점수로 등급을 다시 나누면 governance-worker(알림)와 구간이 두 벌이 되고, 한쪽만 바뀌면
 * 같은 상충이 알림에서는 "치명적", 화면에서는 "높음"으로 보인다. 그래서 테스트는 점수와 등급이
 * 지금 구간으로는 맞지 않는 상충(0.91 인데 기록 당시 HIGH)으로 화면이 등급을 따르는지 본다.
 */
const USER: CurrentUser = {
  id: 2,
  email: 'admin@company.com',
  name: '김하늘',
  status: 'ACTIVE',
  organizationId: 1,
  organizationName: '플랫폼본부',
  teams: [],
  roles: ['SYSTEM_ADMIN'],
};

function conflict(overrides: Partial<ConflictItem> = {}): ConflictItem {
  return {
    id: 1,
    sourceDocId: 10,
    sourceDocTitle: '재택근무 지침',
    targetDocId: 20,
    targetDocTitle: '전사 근무제도 개정안',
    sourceClause: '주 2회까지 재택근무를 신청할 수 있다',
    targetClause: '전원 사무실 출근을 의무화한다',
    conflictType: 'DIRECT_CONTRADICTION',
    severityScore: 0.91,
    severityLevel: 'CRITICAL',
    reasoning: '재택 허용과 전원 출근 의무가 정면으로 어긋난다',
    affectedTeams: [],
    status: 'OPEN',
    resolvedBy: null,
    resolutionNote: null,
    resolvedAt: null,
    createdAt: '2026-09-27T00:00:00Z',
    ...overrides,
  };
}

function renderWith(items: ConflictItem[]) {
  vi.spyOn(api, 'conflicts').mockResolvedValue({
    content: items, page: 0, size: 100, totalElements: items.length, totalPages: 1,
  });
  render(
    <GovernanceDashboard
      user={USER}
      focusConflictId={null}
      onFocusConsumed={() => {}}
      onOpenDocument={() => {}}
    />,
  );
}

describe('GovernanceDashboard 심각도 등급', () => {
  afterEach(() => vi.restoreAllMocks());

  it('배지는 점수가 아니라 저장된 등급을 따른다', async () => {
    renderWith([conflict({ severityScore: 0.91, severityLevel: 'HIGH' })]);

    expect(await screen.findByText('높음 (91%)')).toBeInTheDocument();
    expect(screen.queryByText(/치명적 \(91%\)/)).not.toBeInTheDocument();
  });

  it('심각도 필터도 저장된 등급으로 거른다', async () => {
    renderWith([
      conflict({ id: 1, sourceDocTitle: '기록 당시 치명적', severityScore: 0.8, severityLevel: 'CRITICAL' }),
      conflict({ id: 2, sourceDocTitle: '기록 당시 높음', severityScore: 0.91, severityLevel: 'HIGH' }),
    ]);
    await screen.findByText(/기록 당시 치명적/);

    fireEvent.change(screen.getByDisplayValue('모든 심각도'), { target: { value: 'CRITICAL' } });

    expect(screen.getByText(/기록 당시 치명적/)).toBeInTheDocument();
    expect(screen.queryByText(/기록 당시 높음/)).not.toBeInTheDocument();
  });
});
