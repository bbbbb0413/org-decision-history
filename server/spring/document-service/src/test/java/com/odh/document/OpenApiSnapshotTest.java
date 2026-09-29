package com.odh.document;

import com.odh.common.InternalAuth;
import com.odh.common.contract.OpenApiSnapshots;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import software.amazon.awssdk.services.s3.S3Client;

import java.nio.charset.StandardCharsets;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;

/** `contracts/openapi/document-service.yml` 스냅샷. 방침은 {@link OpenApiSnapshots} 참고. */
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
        // S3Presigner 는 네트워크를 쓰지 않지만 자격증명이 비면 생성 자체가 막힌다
        registry.add("storage.access-key", () -> "openapi-snapshot");
        registry.add("storage.secret-key", () -> "openapi-snapshot");
    }

    /**
     * `StorageConfiguration.s3Client` 는 빈을 만들면서 버킷 존재 여부를 실제로 물어본다 —
     * 스펙을 뜨는 데 객체 저장소가 필요하지도 않은데 MinIO 가 떠 있어야 컨텍스트가 뜬다.
     * 팩터리 메서드가 아예 돌지 않도록 빈을 갈아 끼운다.
     */
    @MockitoBean
    private S3Client s3Client;

    @Autowired
    private MockMvc mockMvc;

    @Test
    void 커밋된_스펙이_지금_코드와_같다() throws Exception {
        // YAML 응답에 charset 이 없어 getContentAsString() 은 한글을 깨뜨린다. 바이트를 직접 읽는다.
        byte[] actual = mockMvc.perform(get("/v3/api-docs.yaml")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY))
                .andReturn().getResponse().getContentAsByteArray();

        OpenApiSnapshots.assertMatches("document-service", new String(actual, StandardCharsets.UTF_8));
    }
}
