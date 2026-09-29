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

    // 스키마는 Flyway 가 소유한다.
    implementation("org.flywaydb:flyway-core")
    runtimeOnly("org.flywaydb:flyway-database-postgresql")

    // 운영 현황의 Redis 연결 확인
    implementation("org.springframework.boot:spring-boot-starter-data-redis")

    implementation(libs.nimbusJose)
    implementation(libs.springCrypto)
    runtimeOnly(libs.postgresql)

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation(testFixtures(project(":server:spring:common")))
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
}
