package com.odh.document;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import io.swagger.v3.oas.annotations.OpenAPIDefinition;
import io.swagger.v3.oas.annotations.info.Info;

@SpringBootApplication
// 스펙 제목·버전을 고정한다. 안 주면 springdoc 기본값("OpenAPI definition"/v0)이 그대로 나가
// contracts/openapi 의 파일 셋을 구분할 수 없다.
@OpenAPIDefinition(info = @Info(title = "document-service API", version = "0.1.0",
        description = "문서 CRUD·파일·일괄 가져오기, 상충 조회·처리, 결정 그래프"))
public class DocumentServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(DocumentServiceApplication.class, args);
    }
}
