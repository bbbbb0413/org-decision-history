# syntax=docker/dockerfile:1.7
# 재순위화 추론 서버. Hugging Face TEI(Text Embeddings Inference) 공식 CPU 이미지에 모델을 넣는다.
# 빌드 컨텍스트는 저장소 루트다(docker-bake.hcl _common).
#
# - TEI CPU 이미지는 아키텍처마다 태그가 따로다(cpu-* = amd64, cpu-arm64-* = arm64). 빌드하는 플랫폼에 맞는
#   베이스를 골라, 개발 Mac(arm64)·CI·운영(amd64) 어디서든 같은 Dockerfile·compose 로 같은 모델이 뜨게 한다.
# - 모델은 저장소 커밋 해시(리비전)로 받는다. 같은 모델 ID 라도 저장소가 바뀌면 다른 가중치가 되기 때문이다.
#   실행 중에는 인터넷을 쓰지 않는다(컨테이너 파일시스템은 읽기 전용이다).
# - 모델을 바꾸려면 아래 세 ARG 를 바꾸고 평가(server/python/rag-service/evaluation)를 다시 돈다.

ARG TEI_VERSION=1.9.4
# BAAI/bge-reranker-base 를 ONNX 로 변환·int8 양자화한 것(Xenova 저장소). 원본 float32(1.1GB) 대비 1/4 크기다.
# 한국어 평가셋 44문항 MRR 0.922(float32 0.936), CPU 1개에서 후보 35건 3.3초, 메모리 약 750MiB(측정:
# server/python/rag-service/evaluation)
ARG RERANKER_MODEL=Xenova/bge-reranker-base
ARG RERANKER_REVISION=280bcc27a84e0b898c251e06fddb25171bd9b101
# 저장소 안에서 받을 ONNX 가중치 파일. 이미지 안에서는 항상 onnx/model.onnx 로 둔다(TEI 가 찾는 이름)
ARG RERANKER_ONNX_FILE=onnx/model_quantized.onnx

# 모델 파일은 아키텍처와 무관하므로 빌드 머신 플랫폼에서 한 번만 받는다
FROM --platform=$BUILDPLATFORM alpine:3.20 AS model
ARG RERANKER_MODEL
ARG RERANKER_REVISION
ARG RERANKER_ONNX_FILE
RUN apk add --no-cache curl
# ONNX 가중치 하나만 넣는다. TEI 는 onnx/model.onnx 가 있으면 ONNX Runtime 백엔드로 돈다(측정도 이 백엔드로 했다).
# 다른 형식·정밀도의 가중치는 넣지 않는다 — 이미지가 커지고 백엔드가 환경마다 갈릴 수 있다
RUN set -eu; mkdir -p /model/onnx; \
    base="https://huggingface.co/${RERANKER_MODEL}/resolve/${RERANKER_REVISION}"; \
    for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json; do \
      curl -fsSL --retry 3 -o "/model/$f" "$base/$f"; \
    done; \
    curl -fsSL --retry 3 -o /model/onnx/model.onnx "$base/${RERANKER_ONNX_FILE}"

FROM ghcr.io/huggingface/text-embeddings-inference:cpu-${TEI_VERSION} AS base-amd64
FROM ghcr.io/huggingface/text-embeddings-inference:cpu-arm64-${TEI_VERSION} AS base-arm64

# TARGETARCH 는 BuildKit 이 빌드 대상 플랫폼으로 채우는 전역 인자다(amd64·arm64)
FROM base-${TARGETARCH}
ARG RERANKER_MODEL
ARG RERANKER_REVISION
COPY --from=model /model /models/reranker
LABEL org.opencontainers.image.title="reranker-service" \
      ai.history.model.id="${RERANKER_MODEL}" \
      ai.history.model.revision="${RERANKER_REVISION}" \
      ai.history.model.file="${RERANKER_ONNX_FILE}"
# 옵션은 TEI 의 환경변수 이름 그대로다(text-embeddings-router --help). served-model-name 은 /info 에 실려
# 운영 중인 모델·리비전을 물으면 답이 나오게 한다
ENV MODEL_ID=/models/reranker \
    SERVED_MODEL_NAME="${RERANKER_MODEL}@${RERANKER_REVISION}" \
    PORT=8090 \
    MAX_CLIENT_BATCH_SIZE=64 \
    AUTO_TRUNCATE=true \
    HF_HUB_OFFLINE=1
USER 10001:10001
EXPOSE 8090
