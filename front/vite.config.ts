/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // 소스와 나란히 두는 *.test.ts(x) 만 테스트로 본다
    include: ['src/**/*.test.{ts,tsx}'],
  },
  build: {
    // 운영 빌드에서 소스맵을 만들지 않는다. 이미지에 소스가 남지 않게 하기 위한 설정이다.
    sourcemap: false,
    outDir: 'dist',
  },
  server: {
    port: 5173,
    // 게이트웨이를 직접 호출한다. 대상 주소는 VITE_GATEWAY_URL 로 지정한다.
  },
});
