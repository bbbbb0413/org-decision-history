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

    // 문서 변경 이벤트를 Redis Stream 으로 발행한다
    implementation("org.springframework.boot:spring-boot-starter-data-redis")

    // 스키마는 Flyway 가 소유한다. document-service 는 문서 관련 테이블만 관리한다.
    implementation("org.flywaydb:flyway-core")
    runtimeOnly("org.flywaydb:flyway-database-postgresql")

    // 원본 파일 보관과 서명된 다운로드 경로 발급
    implementation(libs.awsS3)
    // 확장자가 아니라 파일 시그니처로 형식을 검사한다
    implementation(libs.tikaCore)
    // 일괄 가져오기의 제목·작성자·작성일 추출
    implementation(libs.pdfbox)
    implementation(libs.poiOoxml)

    runtimeOnly(libs.postgresql)
    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation(testFixtures(project(":server:spring:common")))
    testImplementation("org.springframework.boot:spring-boot-testcontainers")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
}
