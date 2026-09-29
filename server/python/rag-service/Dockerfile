# syntax=docker/dockerfile:1.7
# 빌드 컨텍스트는 저장소 루트다 (다른 server/* Dockerfile과 통일).

FROM python:3.12-slim AS deps
WORKDIR /build
COPY server/python/rag-service/requirements.txt ./
RUN pip install --no-cache-dir --target=/deps -r requirements.txt
# 공통 패키지는 의존성 레이어 뒤에 따로 설치한다. shared 만 바뀌었을 때 위 레이어를 다시 받지 않는다.
# shared 의 런타임 의존성은 requirements.txt 가 모두 고정하므로 --no-deps 로 넣는다.
COPY server/python/shared ./shared
RUN pip install --no-cache-dir --no-deps --target=/deps ./shared

FROM python:3.12-slim AS runtime
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=deps /deps /usr/local/lib/python3.12/site-packages
COPY server/python/rag-service/app ./app
COPY server/python/rag-service/tests ./tests
USER app
# HTTP 포트를 열지 않는다 — Kafka consumer로만 동작한다.
ENTRYPOINT ["python", "-m", "app.main"]
