# syntax=docker/dockerfile:1.7
# 빌드 컨텍스트는 저장소 루트다. Gradle 멀티모듈이 루트의 settings.gradle.kts 를 필요로 한다.
# 제외 목록은 루트 .dockerignore 가 담당한다.

########## 1) deps — 의존성 레이어 고정 ##########
FROM eclipse-temurin:21-jdk-alpine AS deps
WORKDIR /build
COPY gradlew settings.gradle.kts build.gradle.kts ./
COPY gradle/ gradle/
# settings.gradle.kts 가 다섯 모듈을 모두 include 하므로 각 프로젝트 디렉터리가 존재해야 한다.
# 빌드 파일만 복사하고 소스는 다음 스테이지에서 대상 모듈만 가져온다.
COPY server/spring/common/build.gradle.kts           server/spring/common/
COPY server/spring/gateway/build.gradle.kts          server/spring/gateway/
COPY server/spring/auth-service/build.gradle.kts     server/spring/auth-service/
COPY server/spring/document-service/build.gradle.kts server/spring/document-service/
COPY server/spring/chat-service/build.gradle.kts     server/spring/chat-service/
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew :server:spring:gateway:dependencies --no-daemon

########## 2) build — 소스 컴파일 ##########
FROM deps AS build
COPY server/spring/common/src       server/spring/common/src
COPY server/spring/gateway/src server/spring/gateway/src
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew :server:spring:gateway:bootJar --no-daemon -x test

########## 3) extract — layered jar 분해 ##########
FROM eclipse-temurin:21-jre-alpine AS extract
WORKDIR /stage
COPY --from=build /build/server/spring/gateway/build/libs/*.jar app.jar
# --launcher 를 주어야 JarLauncher 로 실행하는 전개 형태가 나온다.
# 이 옵션이 없으면 lib/ 를 참조하는 얇은 jar 만 만들어져 JarLauncher 를 찾지 못한다.
RUN java -Djarmode=tools -jar app.jar extract --layers --launcher --destination /stage/layers

########## 4) runtime — 실행에 필요한 것만 ##########
FROM eclipse-temurin:21-jre-alpine AS runtime
RUN addgroup -S -g 10001 app \
 && adduser  -S -u 10001 -G app -H -s /sbin/nologin app
WORKDIR /app
COPY --from=extract --chown=app:app /stage/layers/dependencies/          ./
COPY --from=extract --chown=app:app /stage/layers/spring-boot-loader/    ./
COPY --from=extract --chown=app:app /stage/layers/snapshot-dependencies/ ./
COPY --from=extract --chown=app:app /stage/layers/application/           ./
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-XX:+ExitOnOutOfMemoryError", \
            "org.springframework.boot.loader.launch.JarLauncher"]
