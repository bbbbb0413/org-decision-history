# syntax=docker/dockerfile:1.7
# 빌드 컨텍스트는 저장소 루트다.

FROM python:3.12-slim AS deps
WORKDIR /build
COPY server/python/index-worker/requirements.txt ./
RUN pip install --no-cache-dir --target=/deps -r requirements.txt
# 공통 패키지는 의존성 레이어 뒤에 따로 설치한다. shared 만 바뀌었을 때 위 레이어를 다시 받지 않는다.
COPY server/python/shared ./shared
RUN pip install --no-cache-dir --no-deps --target=/deps ./shared

FROM python:3.12-slim AS runtime
# compose 헬스체크가 wget을 쓴다(Alpine 기반 다른 이미지와 같은 방식). debian slim에는 기본으로 없다.
RUN apt-get update && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 app && useradd -u 10001 -g app -M -s /usr/sbin/nologin app
WORKDIR /app
COPY --from=deps /deps /usr/local/lib/python3.12/site-packages
COPY server/python/index-worker/app ./app
USER app
EXPOSE 8083
ENTRYPOINT ["python", "-m", "app.main"]
