package com.odh.document.storage;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.*;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

import java.net.URI;

@Configuration
@EnableConfigurationProperties(StorageProperties.class)
public class StorageConfiguration {

    private static final Logger log = LoggerFactory.getLogger(StorageConfiguration.class);

    private StaticCredentialsProvider credentials(StorageProperties p) {
        return StaticCredentialsProvider.create(
                AwsBasicCredentials.create(p.accessKey(), p.secretKey()));
    }

    @Bean
    public S3Client s3Client(StorageProperties p) {
        S3Client client = S3Client.builder()
                .endpointOverride(URI.create(p.endpoint()))
                .region(Region.of(p.region()))
                .credentialsProvider(credentials(p))
                // MinIO 는 가상 호스트 방식 주소를 쓰지 않는다
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build())
                .build();
        ensureBucket(client, p.bucket());
        return client;
    }

    /**
     * 서명 경로 발급 전용. 브라우저가 접근할 주소로 서명해야 한다.
     * SigV4 는 호스트를 서명에 포함하므로 내부 주소로 서명하면 사용자 쪽에서 403 이 난다.
     */
    @Bean
    public S3Presigner s3Presigner(StorageProperties p) {
        return S3Presigner.builder()
                .endpointOverride(URI.create(p.publicEndpoint()))
                .region(Region.of(p.region()))
                .credentialsProvider(credentials(p))
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build())
                .build();
    }

    /** 버킷이 없으면 만든다. 개발 환경에서 수동 준비 단계를 없애기 위한 처리다. */
    private void ensureBucket(S3Client client, String bucket) {
        try {
            client.headBucket(HeadBucketRequest.builder().bucket(bucket).build());
        } catch (S3Exception e) {   // NoSuchBucketException 은 S3Exception 의 하위 타입이다
            client.createBucket(CreateBucketRequest.builder().bucket(bucket).build());
            log.info("원본 파일 버킷을 생성했다: {}", bucket);
        }
    }
}
