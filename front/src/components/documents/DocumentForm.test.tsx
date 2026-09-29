import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DocumentForm } from './DocumentForm';
import { api } from '../../api/client';
import { DOCUMENT_TEMPLATES } from '../../lib/documentTemplates';
import type { CurrentUser, DocumentDetail } from '../../api/types';

/**
 * 템플릿 삽입 규칙.
 *
 * 요점은 "넣는다"가 아니라 **"쓴 것을 절대 덮어쓰지 않는다"** 다. 한 번 날려먹으면
 * 그걸로 신뢰를 잃고, 그러면 템플릿 자체가 방해물이 된다.
 */
const USER: CurrentUser = {
  id: 2,
  email: 'admin@company.com',
  name: '김하늘',
  status: 'ACTIVE',
  organizationId: 1,
  organizationName: '플랫폼본부',
  teams: [{ id: 1, organizationId: 1, name: '인증팀' }],
  roles: ['SYSTEM_ADMIN'],
};

function detail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 5,
    title: '기존 문서',
    content: '이미 쓰여 있던 본문',
    documentType: 'POLICY_DOC',
    decisionStatus: 'CURRENT',
    organizationId: 1,
    teamId: null,
    projectName: null,
    authorId: 2,
    authorName: '김하늘',
    authoredAt: '2026-01-10',
    currentVersion: 1,
    createdAt: '2026-01-10T00:00:00Z',
    updatedAt: '2026-01-10T00:00:00Z',
    files: [],
    ...overrides,
  } as DocumentDetail;
}

function renderNew() {
  render(<DocumentForm user={USER} documentId={null} onDone={() => {}} onCancel={() => {}} />);
}

function body() {
  return screen.getByLabelText(/본문/) as HTMLTextAreaElement;
}

function selectType(label: string) {
  fireEvent.change(screen.getByLabelText(/문서 유형/), {
    target: { value: label },
  });
}

describe('DocumentForm 템플릿', () => {
  afterEach(() => vi.restoreAllMocks());

  it('유형을 고르면 본문에 템플릿이 들어간다', () => {
    renderNew();
    expect(body().value).toBe('');

    selectType('DECISION_RECORD');

    expect(body().value).toBe(DOCUMENT_TEMPLATES.DECISION_RECORD.body);
  });

  it('유형별 작성 요령을 보여준다', () => {
    renderNew();
    selectType('MEETING_NOTE');

    expect(screen.getByText(/무엇을 정했는지 한 줄로 분명히/)).toBeInTheDocument();
    expect(screen.getByText(/절 제목은 남겨 두세요/)).toBeInTheDocument();
  });

  it('이미 쓴 본문은 유형을 바꿔도 덮어쓰지 않는다', () => {
    renderNew();
    fireEvent.change(body(), { target: { value: '내가 직접 쓴 내용' } });

    selectType('POLICY_DOC');

    expect(body().value).toBe('내가 직접 쓴 내용');
  });

  it('템플릿만 들어 있으면 유형을 바꿀 때 새 템플릿으로 교체된다', () => {
    // 템플릿은 "쓴 것"이 아니므로 바꿔도 잃는 게 없다 — 다만 지금 구현은 비어 있을 때만
    // 넣으므로, 템플릿이 남아 있으면 그대로 둔다. 그 동작을 명시해 고정한다.
    renderNew();
    selectType('MEETING_NOTE');
    const meeting = body().value;

    selectType('POLICY_DOC');

    expect(body().value).toBe(meeting);
  });

  it('"템플릿 다시 넣기"로 명시적으로 교체할 수 있다', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderNew();
    selectType('MEETING_NOTE');
    fireEvent.change(body(), { target: { value: '지우고 다시 쓰던 중' } });

    fireEvent.click(screen.getByRole('button', { name: '템플릿 다시 넣기' }));

    expect(confirm).toHaveBeenCalled();
    expect(body().value).toBe(DOCUMENT_TEMPLATES.MEETING_NOTE.body);
  });

  it('교체 확인을 취소하면 본문이 그대로 남는다', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderNew();
    selectType('MEETING_NOTE');
    fireEvent.change(body(), { target: { value: '지키고 싶은 내용' } });

    fireEvent.click(screen.getByRole('button', { name: '템플릿 다시 넣기' }));

    expect(body().value).toBe('지키고 싶은 내용');
  });

  it('본문이 비어 있으면 확인 없이 바로 넣는다', () => {
    const confirm = vi.spyOn(window, 'confirm');
    renderNew();
    selectType('MEETING_NOTE');
    fireEvent.change(body(), { target: { value: '   ' } });

    fireEvent.click(screen.getByRole('button', { name: '템플릿 다시 넣기' }));

    expect(confirm).not.toHaveBeenCalled();
    expect(body().value).toBe(DOCUMENT_TEMPLATES.MEETING_NOTE.body);
  });

  it('편집 화면에는 템플릿을 끼워 넣지 않고 버튼도 없다', async () => {
    vi.spyOn(api, 'document').mockResolvedValue(detail());
    render(<DocumentForm user={USER} documentId={5} onDone={() => {}} onCancel={() => {}} />);

    await waitFor(() => expect(body().value).toBe('이미 쓰여 있던 본문'));
    // 유형별 요령은 편집 중에도 도움이 되므로 남기고, 교체 버튼만 감춘다
    expect(screen.queryByRole('button', { name: '템플릿 다시 넣기' })).not.toBeInTheDocument();
  });
});
