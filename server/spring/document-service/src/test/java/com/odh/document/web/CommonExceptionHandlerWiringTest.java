package com.odh.document.web;

import com.odh.common.InternalAuth;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import software.amazon.awssdk.services.s3.S3Client;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * {@code CommonExceptionHandler} 가 실제로 등록돼 있는지 확인한다.
 *
 * <p>이 테스트가 있는 이유: 핸들러를 common 에 만들어 두기만 하고 서비스에서
 * {@code @Import} 하지 않아 한 번도 동작하지 않은 적이 있다. common 은 각 서비스의
 * 컴포넌트 스캔 대상이 아니라서 파일만 있으면 조용히 빠진다 — 컴파일도 통과하고
 * 단위 테스트도 통과하고, E2E 에서 Spring 기본 오류 형태가 나가는 걸로만 드러났다.
 * 상태 코드(400)는 등록 전에도 같았으므로, 코드가 아니라 <b>응답 본문 형태</b>를 본다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Testcontainers
class CommonExceptionHandlerWiringTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    private static final String INTERNAL_KEY = "exception-handler-key";

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
        registry.add("internal.api-key", () -> INTERNAL_KEY);
        registry.add("storage.access-key", () -> "exception-handler");
        registry.add("storage.secret-key", () -> "exception-handler");
    }

    /** OpenApiSnapshotTest 와 같은 이유 — 빈 생성이 MinIO 에 버킷을 물어본다. */
    @MockitoBean
    private S3Client s3Client;

    @Autowired
    private MockMvc mockMvc;

    @Test
    @DisplayName("깨진 JSON 은 프로젝트 공통 오류 형태로 나간다")
    void malformedJsonUsesProjectErrorShape() throws Exception {
        mockMvc.perform(patch("/api/governance/conflicts/1")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY)
                        .header(InternalAuth.USER_ID_HEADER, 1)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\": "))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.message").exists())
                // Spring 기본 형태에만 있는 필드. 이게 보이면 핸들러가 안 걸린 것이다.
                .andExpect(jsonPath("$.timestamp").doesNotExist());
    }

    @Test
    @DisplayName("@Valid 위반은 필드별 사유를 담아 나간다")
    void validationFailureCarriesFieldErrors() throws Exception {
        mockMvc.perform(patch("/api/governance/conflicts/1")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY)
                        .header(InternalAuth.USER_ID_HEADER, 1)
                        .contentType(MediaType.APPLICATION_JSON)
                        // resolutionNote 가 빠졌다 — 사유 없는 상충 처리를 막는다.
                        .content("{\"status\":\"RESOLVED\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.details[0].field").value("resolutionNote"));
    }

    @Test
    @DisplayName("경로 변수 타입 불일치도 공통 형태로 나간다")
    void pathVariableTypeMismatch() throws Exception {
        mockMvc.perform(get("/api/documents/문서")
                        .header(InternalAuth.API_KEY_HEADER, INTERNAL_KEY)
                        .header(InternalAuth.USER_ID_HEADER, 1))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.timestamp").doesNotExist());
    }
}
