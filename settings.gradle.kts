plugins {
    // JDK 21 툴체인을 로컬에 없으면 자동으로 내려받는다.
    // 로컬 JDK 버전과 무관하게 컨테이너 이미지(temurin 21)와 같은 버전으로 빌드하기 위한 설정이다.
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "org-decision-history"

include(
    "server:spring:common",
    "server:spring:gateway",
    "server:spring:auth-service",
    "server:spring:document-service",
    "server:spring:chat-service",
)
