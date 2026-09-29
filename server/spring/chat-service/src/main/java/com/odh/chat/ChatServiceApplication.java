package com.odh.chat;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import io.swagger.v3.oas.annotations.OpenAPIDefinition;
import io.swagger.v3.oas.annotations.info.Info;

@SpringBootApplication
// 스펙 제목·버전을 고정한다. 안 주면 springdoc 기본값("OpenAPI definition"/v0)이 그대로 나가
// contracts/openapi 의 파일 셋을 구분할 수 없다.
@OpenAPIDefinition(info = @Info(title = "chat-service API", version = "0.1.0",
        description = "대화·메시지, 답변 스트리밍(SSE), 출처 재조회, 타임라인"))
public class ChatServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(ChatServiceApplication.class, args);
    }
}
