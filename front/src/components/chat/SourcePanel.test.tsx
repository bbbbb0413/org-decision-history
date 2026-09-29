import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SourcePanel } from './SourcePanel';
import type { AnswerSource } from '../../api/types';

/**
 * 원문 뷰어는 지연 로딩된다. 정적 import 로 되돌아가면 번들이 다시
 * 합쳐지고, 반대로 `lazy()` 의 default 변환을 틀리면 눌러도 아무것도 안 뜬다 —
 * 빌드 산출물만으로는 후자를 못 잡으므로 실제로 열리는지까지 본다.
 *
 * pdfjs 는 jsdom 에서 실제로 렌더할 수 없어 모듈을 통째로 목으로 바꾼다. 여기서 확인할
 * 것은 PDF 렌더가 아니라 "눌렀을 때 청크를 받아 컴포넌트가 실제로 마운트되는가"다.
 */
vi.mock('./SourcePdfModal', () => ({
  SourcePdfModal: ({ source }: { source: AnswerSource }) => (
    <div data-testid="pdf-modal">원문 위치: {source.documentTitle}</div>
  ),
}));

const SOURCE: AnswerSource = {
  documentId: 1,
  documentTitle: '재택근무 운영 지침',
  documentType: 'POLICY_DOC',
  documentVersion: 1,
  chunkNo: 0,
  sectionPath: null,
  chunkTag: 'DECISION',
  similarity: 0.92,
  rank: 1,
  authoredAt: '2026-01-10',
  fileId: null,
  pageStart: null,
  pageEnd: null,
  bboxes: null,
};

describe('SourcePanel', () => {
  afterEach(() => vi.restoreAllMocks());

  it('원문 위치를 누르기 전에는 뷰어를 마운트하지 않는다', () => {
    render(<SourcePanel sources={[SOURCE]} messageId={1} onOpenDocument={() => {}} />);

    expect(screen.queryByTestId('pdf-modal')).not.toBeInTheDocument();
  });

  it('원문 위치를 누르면 지연 로딩된 뷰어가 뜬다', async () => {
    render(<SourcePanel sources={[SOURCE]} messageId={1} onOpenDocument={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: '원문 위치' }));

    expect(await screen.findByTestId('pdf-modal')).toHaveTextContent('재택근무 운영 지침');
  });
});
