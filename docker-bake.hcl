# 컨테이너 이미지 빌드 정의. CI(ci-images.yml)와 로컬이 같은 정의를 쓴다.
# 빌드 컨텍스트는 저장소 루트다 (compose.yml 과 동일).
#
#   docker buildx bake --print                     # 정의 확인
#   docker buildx bake auth-service front --load   # 일부만 빌드해 로컬 docker 에 적재
#
# 이미지를 추가·이동하면 ci-images.yml 의 filters 에도 같은 이름으로 영향 경로를 적는다.

variable "IMAGE_TAG" {
  default = "dev"
}

# 프론트 정적 파일에 빌드 시점에 굳혀 들어가는 게이트웨이 주소 (compose.yml 의 build.args 와 같은 값)
variable "VITE_GATEWAY_URL" {
  default = "http://localhost:8080"
}

group "default" {
  targets = [
    "gateway", "auth-service", "document-service", "chat-service",
    "rag-service", "index-worker", "governance-worker", "front",
  ]
}

target "_common" {
  context = "."
}

target "gateway" {
  inherits   = ["_common"]
  dockerfile = "server/spring/gateway/Dockerfile"
  tags       = ["history/gateway:${IMAGE_TAG}"]
}

target "auth-service" {
  inherits   = ["_common"]
  dockerfile = "server/spring/auth-service/Dockerfile"
  tags       = ["history/auth-service:${IMAGE_TAG}"]
}

target "document-service" {
  inherits   = ["_common"]
  dockerfile = "server/spring/document-service/Dockerfile"
  tags       = ["history/document-service:${IMAGE_TAG}"]
}

target "chat-service" {
  inherits   = ["_common"]
  dockerfile = "server/spring/chat-service/Dockerfile"
  tags       = ["history/chat-service:${IMAGE_TAG}"]
}

target "rag-service" {
  inherits   = ["_common"]
  dockerfile = "server/python/rag-service/Dockerfile"
  tags       = ["history/rag-service:${IMAGE_TAG}"]
}

# 색인 워커(Python)
target "index-worker" {
  inherits   = ["_common"]
  dockerfile = "server/python/index-worker/Dockerfile"
  tags       = ["history/index-worker:${IMAGE_TAG}"]
}

target "governance-worker" {
  inherits   = ["_common"]
  dockerfile = "server/python/governance-worker/Dockerfile"
  tags       = ["history/governance-worker:${IMAGE_TAG}"]
}

target "front" {
  inherits   = ["_common"]
  dockerfile = "front/Dockerfile"
  args       = { VITE_GATEWAY_URL = VITE_GATEWAY_URL }
  tags       = ["history/front:${IMAGE_TAG}"]
}
