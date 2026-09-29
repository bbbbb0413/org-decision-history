plugins {
    alias(libs.plugins.springBoot)
}

dependencies {
    implementation(project(":server:spring:common"))
    // Spring Cloud Gateway 는 WebFlux 위에서 동작한다. starter-web 을 함께 넣으면
    // Boot 가 Servlet 스택을 선택해 게이트웨이가 기동하지 않는다.
    // 4.3 에서 spring-cloud-starter-gateway 는 폐기되었다. 서버 구현을 명시하는 이름을 쓴다.
    implementation("org.springframework.cloud:spring-cloud-starter-gateway-server-webflux")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    // /actuator/prometheus 노출용 레지스트리.
    // 런타임에만 있으면 actuator 가 알아서 엔드포인트를 만든다 — 코드에서 직접 참조하지 않는다.
    runtimeOnly("io.micrometer:micrometer-registry-prometheus")
    // 로그인 rate limiting(RedisRateLimiter)에 쓴다
    implementation("org.springframework.boot:spring-boot-starter-data-redis-reactive")
    implementation(libs.nimbusJose)
    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("io.projectreactor:reactor-test")
}
