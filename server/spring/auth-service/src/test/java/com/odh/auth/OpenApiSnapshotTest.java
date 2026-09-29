package com.odh.auth;

import com.odh.common.InternalAuth;
import com.odh.common.contract.OpenApiSnapshots;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

/**
 * `contracts/openapi/auth-service.yml` 이 지금 코드와 맞는지 확인하고, 어긋나면 새로 쓴다
 *
 * <p>스펙은 코드에서 뽑아 커밋하고, API 가 바뀌면 이 테스트가 파일을 고쳐 diff 로 드러낸다 —
 * `contracts/indexing/golden/` 의 골든 스냅샷과 같은 방식이다.
 *
 * <p>CI(`CI=true`)에서는 고쳐 쓰지 않고 실패시킨다. 스펙을 다시 떠서 커밋하는 건 사람이
 * 할 일이고, CI 가 조용히 덮어써 버리면 계약 변경이 리뷰를 건너뛴다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class OpenApiSnapshotTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    private static final String INTERNAL_KEY = "openapi-snapshot-key";

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
        registry.add("internal.api-key", () -> INTERNAL_KEY);
    }

    @Autowired
    private MockMvc mockMvc;

    @Test
    void 커밋된_스펙이_지금_코드와_같다() throws Exception {
        // getContentAsString() 은 응답이 선언한 인코딩을 따르는데 YAML 응답에는 charset 이
        // 없어 ISO-8859-1 로 읽힌다 — 한글 설명이 깨진다. 바이트를 UTF-8 로 직접 읽는다.
        byte[] actual = mockMvc.perform(get("/v3/api-docs.yaml")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY))
                .andReturn().getResponse().getContentAsByteArray();

        OpenApiSnapshots.assertMatches("auth-service", new String(actual, StandardCharsets.UTF_8));
    }
}
