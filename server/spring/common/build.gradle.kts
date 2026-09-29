plugins {
    // 이 모듈의 타입이 각 서비스의 공개 API 에 그대로 노출되므로 api 구성을 쓴다.
    `java-library`
    // OpenAPI 스냅샷 비교 도우미를 세 서비스 테스트가 공유한다.
    // 테스트 전용이라 운영 산출물(jar)에는 들어가지 않는다.
    `java-test-fixtures`
}

// 게이트웨이는 WebFlux, 나머지 서비스는 Servlet 이다.
// 이 모듈이 어느 한쪽 웹 스타터에 의존하면 반대편 런타임이 깨진다.
// spring-web 은 두 스택 모두 포함하므로 안전하다. 서블릿 전용 필터만 compileOnly 로 받는다.
dependencies {
    api("org.springframework.boot:spring-boot-starter")
    api("com.fasterxml.jackson.core:jackson-annotations")
    // 임베딩 클라이언트가 RestClient 를 쓴다.
    // index-worker 와 chat-service 가 같은 모델·차원을 써야 검색이 성립하므로 한곳에 두고 공유한다.
    api("org.springframework:spring-web")
    // PermissionScopeClient 의 유저별 캐시가 TTL만 있고 최대 크기가 없어 장기 실행 시
    // 서서히 메모리가 늘었다. maximumSize 로 상한을 둔다
    implementation(libs.caffeine)

    compileOnly("jakarta.servlet:jakarta.servlet-api")

    testFixturesImplementation("org.assertj:assertj-core")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    // 이 모듈은 Spring Boot Gradle 플러그인을 적용하지 않아(라이브러리라 부트 가능한 앱이 아니다)
    // 그 플러그인이 자동으로 얹어주는 junit-platform-launcher 가 빠진다. 없으면 테스트 실행 자체가
    // "Failed to load JUnit Platform" 으로 죽는다.
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}
