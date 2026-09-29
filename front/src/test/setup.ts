import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

/**
 * Node 26 부터 globalThis.localStorage 가 내장으로 생겼는데 `--localstorage-file` 없이는
 * undefined 이고, vitest 의 jsdom 환경에서는 window 가 곧 globalThis 라 jsdom 이 주는
 * localStorage 까지 같이 가려진다. 그 결과 앱 코드의 localStorage 접근이 테스트에서만 깨진다.
 *
 * 브라우저와 같은 동작만 있으면 되므로 없을 때만 최소 구현을 끼워 넣는다
 * (localStorage 가 정상인 Node 버전·환경에서는 아무것도 하지 않는다).
 */
if (!globalThis.localStorage) {
  const store = new Map<string, string>();
  const memoryStorage: Storage = {
    get length() { return store.size; },
    key: (index: number) => [...store.keys()][index] ?? null,
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, String(value)); },
    removeItem: (key: string) => { store.delete(key); },
    clear: () => { store.clear(); },
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: memoryStorage,
    configurable: true,
    writable: true,
  });
}

// 테스트마다 DOM 과 localStorage 를 비운다 — 토큰이 다음 테스트로 새면 인증 관련 검증이 서로 오염된다
afterEach(() => {
  cleanup();
  localStorage.clear();
});
