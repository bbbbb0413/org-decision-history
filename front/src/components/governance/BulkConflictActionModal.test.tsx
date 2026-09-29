import { render, screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BulkConflictActionModal } from './BulkConflictActionModal';
import { api, HttpError } from '../../api/client';
import type { ConflictItem } from '../../api/types';

function conflict(id: number): ConflictItem {
  return {
    id,
    sourceDocId: id * 10,
    sourceDocTitle: `신규 문서 ${id}`,
    targetDocId: id * 10 + 1,
    targetDocTitle: `기존 규정 ${id}`,
    sourceClause: '신규 조항',
    targetClause: '기존 조항',
    conflictType: 'DIRECT_CONTRADICTION',
    severityScore: 0.9,
    reasoning: '사유',
    affectedTeams: [],
    status: 'OPEN',
    resolvedBy: null,
    resolutionNote: null,
    resolvedAt: null,
    createdAt: new Date().toISOString(),
  };
}

function fillNoteAndSubmit(count: number) {
  fireEvent.change(screen.getByPlaceholderText(/같은 사유가 기록됩니다/), {
    target: { value: '인사위원회 의결' },
  });
  fireEvent.click(screen.getByRole('button', { name: `${count}건 일괄 조치` }));
}

describe('BulkConflictActionModal', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('선택한 건마다 조치를 보내고 성공하면 닫힌다', () => {
    const resolve = vi.spyOn(api, 'resolveConflict')
      .mockImplementation(async (id) => ({ ...conflict(id), status: 'RESOLVED' }));
    const onSuccess = vi.fn();
    const onClose = vi.fn();
    render(<BulkConflictActionModal conflicts={[conflict(1), conflict(2)]} onClose={onClose} onSuccess={onSuccess} />);

    fillNoteAndSubmit(2);

    return waitFor(() => {
      expect(resolve).toHaveBeenCalledTimes(2);
      expect(resolve).toHaveBeenCalledWith(1, { status: 'RESOLVED', resolutionNote: '인사위원회 의결' });
      expect(onSuccess).toHaveBeenCalledWith(expect.arrayContaining([expect.objectContaining({ id: 1 })]));
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('일부만 실패하면 성공한 건은 반영하고 실패한 건만 사유와 함께 남긴다', async () => {
    // 하나가 실패했다고 나머지까지 되돌리면, 사용자는 무엇이 처리됐는지 알 수 없게 된다
    vi.spyOn(api, 'resolveConflict').mockImplementation(async (id) => {
      if (id === 2) {
        throw new HttpError(403, { code: 'SCOPE_VIOLATION', message: '관리 범위 밖의 요청입니다.' });
      }
      return { ...conflict(id), status: 'RESOLVED' };
    });
    const onSuccess = vi.fn();
    const onClose = vi.fn();
    render(<BulkConflictActionModal conflicts={[conflict(1), conflict(2)]} onClose={onClose} onSuccess={onSuccess} />);

    fillNoteAndSubmit(2);

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith([expect.objectContaining({ id: 1 })]);
    });
    // 실패한 건이 남아 있으므로 모달을 닫지 않는다
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/1건 처리 실패, 1건은 반영됨/)).toBeInTheDocument();
    expect(screen.getByText(/관리 범위 밖의 요청입니다/)).toBeInTheDocument();
  });

  it('사유를 비우면 요청을 보내지 않는다', () => {
    // 조치 사유는 감사 로그에 남는 기록이라 빈 값으로 넘어가면 안 된다
    const resolve = vi.spyOn(api, 'resolveConflict');
    render(<BulkConflictActionModal conflicts={[conflict(1)]} onClose={vi.fn()} onSuccess={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '1건 일괄 조치' }));

    expect(resolve).not.toHaveBeenCalled();
    expect(screen.getByText(/조치 사유 및 근거를 구체적으로 입력해주세요/)).toBeInTheDocument();
  });

  it('예외 허용(DISMISSED)을 고르면 그 상태로 보낸다', () => {
    const resolve = vi.spyOn(api, 'resolveConflict')
      .mockImplementation(async (id) => ({ ...conflict(id), status: 'DISMISSED' }));
    render(<BulkConflictActionModal conflicts={[conflict(1)]} onClose={vi.fn()} onSuccess={vi.fn()} />);

    fireEvent.click(screen.getByRole('radio', { name: /예외 허용/ }));
    fillNoteAndSubmit(1);

    return waitFor(() => {
      expect(resolve).toHaveBeenCalledWith(1, { status: 'DISMISSED', resolutionNote: '인사위원회 의결' });
    });
  });
});
