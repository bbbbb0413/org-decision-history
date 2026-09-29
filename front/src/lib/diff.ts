/**
 * 줄 단위 텍스트 비교.
 *
 * 외부 라이브러리를 쓰지 않는다 — 번들이 이미 단일 청크로 큰 편이고,
 * 필요한 건 고전적인 LCS 줄 비교 하나뿐이다.
 */

export type DiffKind = 'same' | 'added' | 'removed';

export interface DiffRow {
  kind: DiffKind;
  text: string;
  /** 이전 버전에서의 줄 번호(1부터). 추가된 줄이면 null. */
  oldLineNo: number | null;
  /** 이후 버전에서의 줄 번호(1부터). 삭제된 줄이면 null. */
  newLineNo: number | null;
}

export interface DiffResult {
  rows: DiffRow[];
  addedCount: number;
  removedCount: number;
  /** 두 본문이 완전히 같으면 true. */
  identical: boolean;
  /**
   * 줄 수가 상한을 넘어 비교를 포기했다. LCS 표가 줄 수의 곱만큼 커져서,
   * 병적으로 긴 문서를 만나면 브라우저가 멈출 수 있다.
   */
  tooLarge: boolean;
}

/** 이 줄 수를 넘으면 비교하지 않는다. 사내 정책·회의록이 이 정도로 길 일은 없어 넉넉히 잡았다. */
export const MAX_DIFF_LINES = 1500;

export function diffLines(oldText: string, newText: string): DiffResult {
  const a = oldText.split('\n');
  const b = newText.split('\n');

  if (a.length > MAX_DIFF_LINES || b.length > MAX_DIFF_LINES) {
    return { rows: [], addedCount: 0, removedCount: 0, identical: oldText === newText, tooLarge: true };
  }

  const m = a.length;
  const n = b.length;
  const width = n + 1;
  // dp[i][j] = a[i..] 와 b[j..] 의 최장 공통 부분수열 길이. 평탄한 배열 하나로 둔다.
  const dp = new Uint32Array((m + 1) * width);
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i * width + j] = a[i] === b[j]
        ? dp[(i + 1) * width + (j + 1)] + 1
        : Math.max(dp[(i + 1) * width + j], dp[i * width + (j + 1)]);
    }
  }

  const rows: DiffRow[] = [];
  let addedCount = 0;
  let removedCount = 0;
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      rows.push({ kind: 'same', text: a[i], oldLineNo: i + 1, newLineNo: j + 1 });
      i++;
      j++;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      rows.push({ kind: 'removed', text: a[i], oldLineNo: i + 1, newLineNo: null });
      removedCount++;
      i++;
    } else {
      rows.push({ kind: 'added', text: b[j], oldLineNo: null, newLineNo: j + 1 });
      addedCount++;
      j++;
    }
  }
  while (i < m) {
    rows.push({ kind: 'removed', text: a[i], oldLineNo: i + 1, newLineNo: null });
    removedCount++;
    i++;
  }
  while (j < n) {
    rows.push({ kind: 'added', text: b[j], oldLineNo: null, newLineNo: j + 1 });
    addedCount++;
    j++;
  }

  return { rows, addedCount, removedCount, identical: addedCount === 0 && removedCount === 0, tooLarge: false };
}

/**
 * 바뀐 줄 주변만 남기고 접는다. 긴 문서에서 "무엇이 바뀌었나"를 보려면
 * 같은 줄 수백 개를 스크롤해 지나칠 이유가 없다.
 */
export function collapseUnchanged(rows: DiffRow[], context = 3): (DiffRow | { kind: 'gap'; hidden: number })[] {
  const keep = new Array<boolean>(rows.length).fill(false);
  rows.forEach((row, index) => {
    if (row.kind === 'same') return;
    for (let k = Math.max(0, index - context); k <= Math.min(rows.length - 1, index + context); k++) {
      keep[k] = true;
    }
  });

  const output: (DiffRow | { kind: 'gap'; hidden: number })[] = [];
  let hidden = 0;
  rows.forEach((row, index) => {
    if (keep[index]) {
      if (hidden > 0) {
        output.push({ kind: 'gap', hidden });
        hidden = 0;
      }
      output.push(row);
    } else {
      hidden++;
    }
  });
  if (hidden > 0) output.push({ kind: 'gap', hidden });
  return output;
}
