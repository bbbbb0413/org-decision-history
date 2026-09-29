plugins {
    alias(libs.plugins.springBoot)
}

dependencies {
    implementation(project(":server:spring:common"))
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    // /actuator/prometheus 노출용 레지스트리.
    // 런타임에만 있으면 actuator 가 알아서 엔드포인트를 만든다 — 코드에서 직접 참조하지 않는다.
    runtimeOnly("io.micrometer:micrometer-registry-prometheus")
    implementation("org.springframework.boot:spring-boot-starter-validation")

    // OpenAPI 스펙 생성. `-api` 라 Swagger UI 는 딸려오지 않는다 —
    // 스펙은 contracts/openapi 로 떠서 커밋하고, 런타임에 문서 화면을 열지는 않는다.
    implementation(libs.springdocMvc)
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")

    // 대화 요약과 답변 캐시, 답변 job 의 Redis Stream fan-out/resume
    implementation("org.springframework.boot:spring-boot-starter-data-redis")

    // 답변 생성 job 분산
    implementation("org.springframework.kafka:spring-kafka")

    implementation("org.flywaydb:flyway-core")
    runtimeOnly("org.flywaydb:flyway-database-postgresql")
    runtimeOnly(libs.postgresql)

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation(testFixtures(project(":server:spring:common")))
    // TimelineService의 캐시 로직은 Postgres 방언에 기대는 SQL이 많아
    // 실제 Postgres로 검증한다 — document-service/auth-service와 같은 패턴이다.
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
}
