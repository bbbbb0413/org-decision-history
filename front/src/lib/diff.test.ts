import { describe, expect, it } from 'vitest';
import { collapseUnchanged, diffLines, MAX_DIFF_LINES } from './diff';

const kinds = (text: string, other: string) => diffLines(text, other).rows.map((r) => r.kind).join(',');

describe('diffLines', () => {
  it('같은 본문은 변경 없음으로 본다', () => {
    const result = diffLines('a\nb\nc', 'a\nb\nc');

    expect(result.identical).toBe(true);
    expect(result.addedCount + result.removedCount).toBe(0);
    expect(kinds('a\nb\nc', 'a\nb\nc')).toBe('same,same,same');
  });

  it('한 줄 수정은 삭제 + 추가로 나온다', () => {
    const result = diffLines('a\nb\nc', 'a\nB\nc');

    expect(result.addedCount).toBe(1);
    expect(result.removedCount).toBe(1);
    expect(result.rows).toContainEqual({ kind: 'removed', text: 'b', oldLineNo: 2, newLineNo: null });
    expect(result.rows).toContainEqual({ kind: 'added', text: 'B', oldLineNo: null, newLineNo: 2 });
  });

  it('중간 삽입 시 기존 줄은 그대로 유지된다', () => {
    // LCS 가 제대로 동작하지 않으면 뒤 줄들이 통째로 삭제+추가로 밀린다
    expect(kinds('a\nc', 'a\nb\nc')).toBe('same,added,same');
  });

  it('중간 삭제도 같은 방식으로 잡는다', () => {
    expect(kinds('a\nb\nc', 'a\nc')).toBe('same,removed,same');
  });

  it('줄 번호는 양쪽에서 각각 매겨진다', () => {
    const result = diffLines('x\nold\ny', 'x\nnew1\nnew2\ny');
    const last = result.rows[result.rows.length - 1];

    // 마지막 줄 y 는 이전 본문에서 3번째, 이후 본문에서 4번째다
    expect(last).toMatchObject({ kind: 'same', text: 'y', oldLineNo: 3, newLineNo: 4 });
  });

  it('한쪽이 빈 본문이어도 동작한다', () => {
    expect(diffLines('', 'a\nb').addedCount).toBeGreaterThan(0);
    expect(diffLines('a\nb', '').removedCount).toBe(2);
  });

  it('어떤 입력에서도 결과로 양쪽 원본을 복원할 수 있다', () => {
    // diff 의 근본 불변식이다. 이게 깨지면 화면에 없는 내용이 보이거나 있는 내용이 사라진다
    const randomDoc = () =>
      Array.from({ length: 1 + Math.floor(Math.random() * 12) },
        () => 'abcde'[Math.floor(Math.random() * 5)]).join('\n');

    for (let i = 0; i < 200; i++) {
      const before = randomDoc();
      const after = randomDoc();
      const { rows } = diffLines(before, after);

      expect(rows.filter((r) => r.kind !== 'added').map((r) => r.text).join('\n')).toBe(before);
      expect(rows.filter((r) => r.kind !== 'removed').map((r) => r.text).join('\n')).toBe(after);
    }
  });

  it('줄 수가 상한을 넘으면 비교를 포기한다', () => {
    // LCS 표가 줄 수의 곱만큼 커져서, 상한이 없으면 병적으로 긴 문서에 브라우저가 멈춘다
    const huge = Array.from({ length: MAX_DIFF_LINES + 1 }, (_, i) => `line ${i}`).join('\n');

    const result = diffLines(huge, `${huge}\nmore`);

    expect(result.tooLarge).toBe(true);
    expect(result.rows).toEqual([]);
  });
});

describe('collapseUnchanged', () => {
  it('변경 주변만 남기고 접는다', () => {
    const before = Array.from({ length: 30 }, (_, i) => `l${i}`).join('\n');
    const after = Array.from({ length: 30 }, (_, i) => (i === 15 ? 'CHANGED' : `l${i}`)).join('\n');
    const { rows } = diffLines(before, after);

    const collapsed = collapseUnchanged(rows, 3);

    expect(collapsed.length).toBeLessThan(rows.length);
    expect(collapsed.some((r) => r.kind === 'gap')).toBe(true);
    // 바뀐 줄은 접히면 안 된다
    expect(collapsed).toContainEqual(expect.objectContaining({ kind: 'added', text: 'CHANGED' }));
    expect(collapsed).toContainEqual(expect.objectContaining({ kind: 'removed', text: 'l15' }));
  });

  it('변경이 없으면 전부 접는다', () => {
    const { rows } = diffLines('a\nb\nc', 'a\nb\nc');

    expect(collapseUnchanged(rows, 3)).toEqual([{ kind: 'gap', hidden: 3 }]);
  });
});
