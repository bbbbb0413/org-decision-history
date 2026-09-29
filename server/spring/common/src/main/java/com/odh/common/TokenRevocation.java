package com.odh.common;

/**
 * 로그아웃 시 "이 사용자의 이 시각 이전에 발급된 토큰은 전부 무효" 라는 워터마크를
 * Redis에 저장하는 키 규약.
 *
 * jti 블록리스트 대신 사용자별 워터마크를 쓴 이유: jti를 낱개로 모으려면 로그아웃 시점에
 * 그 사용자가 들고 있는 모든 토큰의 jti를 알아야 하는데(액세스·재발급 각각), 서버는
 * 발급한 토큰을 따로 저장하지 않는다(완전히 stateless 발급). 워터마크는 발급 시각(iat)만
 * 비교하면 되므로 발급된 토큰 목록을 몰라도 전부 무효화할 수 있다 — 대신 로그아웃은
 * "이 기기만"이 아니라 그 사용자의 모든 세션을 끝낸다.
 *
 * auth-service(로그아웃 시 씀, blocking StringRedisTemplate)와 gateway(토큰 검증 시 읽음,
 * reactive ReactiveStringRedisTemplate)가 이 키 형식에만 합의하면 되므로, Redis 클라이언트
 * 종류가 서로 달라도(reactive/blocking) 공용 로직을 Java 코드로 공유할 필요가 없다.
 */
public final class TokenRevocation {

    private TokenRevocation() {
    }

    public static String watermarkKey(String userId) {
        return "auth:revoked-before:" + userId;
    }
}
