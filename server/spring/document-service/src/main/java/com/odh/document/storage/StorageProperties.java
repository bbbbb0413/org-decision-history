package com.odh.document.storage;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * @param endpoint       서비스가 파일을 넣고 뺄 때 쓰는 내부 주소
 * @param publicEndpoint 브라우저에 주는 서명 경로의 주소.
 *                       내부 주소로 서명하면 사용자 쪽에서 호스트가 달라 서명 검증에 실패한다
 * @param bucket      원본 파일 버킷
 * @param accessKey   접근 키
 * @param secretKey   비밀 키
 * @param region      S3 SDK 가 요구하는 값. MinIO 에서는 의미가 없다
 * @param urlTtl      다운로드 경로 유효 시간
 * @param maxFileSize 업로드 상한
 */
@ConfigurationProperties(prefix = "storage")
public record StorageProperties(
        String endpoint,
        String publicEndpoint,
        String bucket,
        String accessKey,
        String secretKey,
        String region,
        Duration urlTtl,
        long maxFileSize
) {
    public StorageProperties {
        if (publicEndpoint == null || publicEndpoint.isBlank()) publicEndpoint = endpoint;
        if (region == null || region.isBlank()) region = "us-east-1";
        if (urlTtl == null) urlTtl = Duration.ofSeconds(60);
        if (maxFileSize <= 0) maxFileSize = 50L * 1024 * 1024;
    }
}
