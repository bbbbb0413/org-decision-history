# syntax=docker/dockerfile:1.7
# 빌드 컨텍스트는 저장소 루트다.

########## 1) deps ##########
FROM node:22-alpine AS deps
WORKDIR /build
COPY front/package.json front/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

########## 2) build ##########
FROM deps AS build
# Vite 는 VITE_ 로 시작하는 값을 빌드 시점에 정적 파일에 굳혀 넣는다.
ARG VITE_GATEWAY_URL
ENV VITE_GATEWAY_URL=${VITE_GATEWAY_URL}
COPY front/ ./
RUN npm run build

########## 3) runtime ##########
# nginx-unprivileged 는 UID 101 로 실행되고 8080 포트를 쓴다.
# 별도 사용자 생성 없이 비루트 조건을 만족한다.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --chown=nginx:nginx front/deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=nginx:nginx /build/dist /usr/share/nginx/html
EXPOSE 8080
