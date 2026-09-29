import { lazy } from 'react';

/**
 * 첫 화면에 필요 없는 무거운 뷰를 따로 떼어낸다.
 *
 * - `SourcePdfModal` 은 `pdfjs-dist` 를, `DecisionGraphView` 는 `@xyflow/react`(+ 그 CSS)를
 *   끌고 온다. 둘 다 사용자가 "원문 위치"를 누르거나 그래프를 펼쳤을 때만 필요한데,
 *   정적 import 면 로그인 화면부터 같이 내려받는다.
 * - `DecisionGraphView` 는 ChatView 와 DocumentDetailView 두 곳에서 쓴다. 각자
 *   `lazy()` 를 부르면 같은 청크를 가리키는 컴포넌트가 둘이 되어 로딩 상태를 따로
 *   관리하게 되므로, 여기서 한 번만 만들어 공유한다.
 *
 * 이름 있는 export 를 default 로 바꿔 `lazy()` 가 요구하는 모양에 맞춘다.
 */
export const DecisionGraphViewLazy = lazy(() =>
  import('./DecisionGraphView').then((m) => ({ default: m.DecisionGraphView })),
);

export const SourcePdfModalLazy = lazy(() =>
  import('./SourcePdfModal').then((m) => ({ default: m.SourcePdfModal })),
);
