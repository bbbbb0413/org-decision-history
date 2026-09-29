package com.odh.chat;

import com.odh.common.InternalAuth;
import com.odh.common.contract.OpenApiSnapshots;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.nio.charset.StandardCharsets;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

/** `contracts/openapi/chat-service.yml` 스냅샷. 방침은 {@link OpenApiSnapshots} 참고. */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class OpenApiSnapshotTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    private static final String INTERNAL_KEY = "openapi-snapshot-key";

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("internal.api-key", () -> INTERNAL_KEY);
    }

    @Autowired
    private MockMvc mockMvc;

    @Test
    void 커밋된_스펙이_지금_코드와_같다() throws Exception {
        // YAML 응답에 charset 이 없어 getContentAsString() 은 한글을 깨뜨린다. 바이트를 직접 읽는다.
        byte[] actual = mockMvc.perform(get("/v3/api-docs.yaml")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY))
                .andReturn().getResponse().getContentAsByteArray();

        OpenApiSnapshots.assertMatches("chat-service", new String(actual, StandardCharsets.UTF_8));
    }
}
