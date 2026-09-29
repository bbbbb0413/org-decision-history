import { describe, expect, it } from 'vitest';
import { DOCUMENT_TEMPLATES, TEMPLATE_COMMON_GUIDE, isBlankBody } from './documentTemplates';
import { DOCUMENT_TYPE_LABEL, type DocumentType } from '../api/types';

/**
 * 템플릿이 색인 파이프라인과 맞는지 지킨다.
 *
 * 여기 있는 규칙은 취향이 아니라 계약이다. `contracts/indexing/README.md` §5 의 태그
 * 규칙과 index-worker 의 헤딩 청킹에 직접 물려 있어서, 템플릿 문구를 고치다 아래 조건을
 * 깨면 검색·타임라인이 조용히 나빠진다 — 화면에는 아무 오류도 안 뜬다.
 */

/** contracts/indexing/README.md §5 의 태그 규칙. 우선순위 순서 그대로다. */
const TAG_RULES: Array<[string, RegExp]> = [
  ['DECISION', /결정|채택|확정|승인|선정|합의|reject|approve|decide/i],
  ['ACTION_ITEM', /액션|todo|담당자|기한|follow[- ]?up|action item/i],
  ['DISCUSSION', /논의|검토|의견|대안|비교|제안|discuss|review/i],
];

function tagOf(body: string): string {
  for (const [tag, pattern] of TAG_RULES) {
    if (pattern.test(body)) return tag;
  }
  return 'BACKGROUND';
}

/** index-worker `_split_by_headings` 와 같은 방식으로 `##` 절을 가른다. */
function sections(body: string): Array<{ path: string; text: string }> {
  const lines = body.split('\n');
  const out: Array<{ path: string; text: string }> = [];
  let current: { path: string; text: string } | null = null;
  for (const line of lines) {
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      if (current) out.push(current);
      current = { path: heading[1].trim(), text: line + '\n' };
    } else if (current) {
      current.text += line + '\n';
    }
  }
  if (current) out.push(current);
  return out;
}

const TYPES = Object.keys(DOCUMENT_TYPE_LABEL) as DocumentType[];

describe('문서 템플릿', () => {
  it('모든 문서 유형에 템플릿이 있다', () => {
    for (const type of TYPES) {
      expect(DOCUMENT_TEMPLATES[type], type).toBeDefined();
      expect(DOCUMENT_TEMPLATES[type].body.length, type).toBeGreaterThan(0);
      expect(DOCUMENT_TEMPLATES[type].guide.length, type).toBeGreaterThan(0);
    }
  });

  it.each(TYPES)('%s — 절이 여러 개로 갈린다', (type) => {
    // 헤딩이 없으면 문서 전체가 청크 하나가 되고 section_path 가 빈 문자열이 된다.
    expect(sections(DOCUMENT_TEMPLATES[type].body).length).toBeGreaterThanOrEqual(4);
  });

  it.each(TYPES)('%s — DECISION 으로 태깅되는 절이 최소 하나 있다', (type) => {
    // 타임라인 발췌는 chunk_tag='DECISION' 청크만 쓴다(contracts/indexing/README.md §5).
    // 이게 없으면 문서를 등록해도 타임라인에 내용이 안 뜬다.
    const tags = sections(DOCUMENT_TEMPLATES[type].body).map((s) => tagOf(s.text));
    expect(tags).toContain('DECISION');
  });

  it.each(TYPES)('%s — 절 제목이 서로 다르다', (type) => {
    const paths = sections(DOCUMENT_TEMPLATES[type].body).map((s) => s.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('회의록 — 영향 범위 절이 DECISION 으로 넘어가지 않는다', () => {
    // 예시 문구에 "결정" 을 넣었다가 실제 색인기에서 DECISION 으로 잡힌 적이 있다.
    // 타임라인 발췌가 오염되므로 이 절은 BACKGROUND 로 남아야 한다.
    const impact = sections(DOCUMENT_TEMPLATES.MEETING_NOTE.body)
      .find((s) => s.path === '영향 범위');

    expect(impact).toBeDefined();
    expect(tagOf(impact!.text)).toBe('BACKGROUND');
  });

  it('의사결정 기록 — 대안 절이 DISCUSSION 으로 잡힌다', () => {
    const alternatives = sections(DOCUMENT_TEMPLATES.DECISION_RECORD.body)
      .find((s) => s.path === '검토한 대안');

    expect(tagOf(alternatives!.text)).toBe('DISCUSSION');
  });

  it('공통 안내가 절 제목을 남기라고 말한다', () => {
    expect(TEMPLATE_COMMON_GUIDE).toContain('##');
    // 마크다운으로 렌더하지 않는 자리라 백틱을 넣으면 그대로 보인다
    expect(TEMPLATE_COMMON_GUIDE).not.toContain('`');
  });
});

describe('isBlankBody', () => {
  it('공백·개행만 있으면 비어 있는 것으로 본다', () => {
    expect(isBlankBody('')).toBe(true);
    expect(isBlankBody('   ')).toBe(true);
    expect(isBlankBody('\n\n  \n')).toBe(true);
  });

  it('내용이 있으면 비어 있지 않다', () => {
    expect(isBlankBody('## 배경')).toBe(false);
    expect(isBlankBody('  a  ')).toBe(false);
  });
});
