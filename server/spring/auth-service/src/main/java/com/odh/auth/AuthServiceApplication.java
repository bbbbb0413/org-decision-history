package com.odh.auth;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import io.swagger.v3.oas.annotations.OpenAPIDefinition;
import io.swagger.v3.oas.annotations.info.Info;

@SpringBootApplication
// 스펙 제목·버전을 고정한다. 안 주면 springdoc 기본값("OpenAPI definition"/v0)이 그대로 나가
// contracts/openapi 의 파일 셋을 구분할 수 없다.
@OpenAPIDefinition(info = @Info(title = "auth-service API", version = "0.1.0",
        description = "로그인·JWT 발급, 사용자·조직·권한 관리, 감사, 운영 현황"))
public class AuthServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(AuthServiceApplication.class, args);
    }
}
