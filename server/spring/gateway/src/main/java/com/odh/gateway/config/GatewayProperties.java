package com.odh.gateway.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 하위 서비스 주소와 내부 호출 키.
 *
 * @param authUrl     인증 · 조직 · 권한
 * @param documentUrl 문서 · 원본 · 가져오기 · 외부 저장소
 * @param indexUrl    색인 작업
 * @param chatUrl     대화 · 답변 · 타임라인
 * @param internalKey 하위 서비스가 검증하는 API 키
 * @param jwksUri     서명 검증에 쓸 공개키 위치
 * @param frontOrigin 프론트가 게이트웨이를 직접 호출할 때 CORS 로 허용할 origin. 콤마로 여러 개를 둘 수 있다
 * @param loginRateLimitPerSecond 로그인 요청 허용 속도(초당, 토큰 버킷 보충률). 무차별 대입 공격 방어
 * @param loginRateLimitBurst     로그인 순간 버스트 허용치(버킷 용량)
 */
@ConfigurationProperties(prefix = "gateway")
public record GatewayProperties(
        String authUrl,
        String documentUrl,
        String indexUrl,
        String chatUrl,
        String internalKey,
        String jwksUri,
        String frontOrigin,
        int loginRateLimitPerSecond,
        int loginRateLimitBurst
) {
}
