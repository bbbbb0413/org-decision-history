package com.odh.auth.jwt;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.odh.auth.domain.SystemRole;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Date;
import java.util.List;

/** 액세스·재발급 토큰을 발급한다. 검증은 게이트웨이가 JWKS 로 수행한다. */
@Service
public class TokenService {

    private static final String CLAIM_ROLES = "roles";
    private static final String CLAIM_TYPE = "typ";
    private static final String TYPE_REFRESH = "refresh";

    private final JwtKeyProvider keyProvider;
    private final JwtProperties properties;

    public TokenService(JwtKeyProvider keyProvider, JwtProperties properties) {
        this.keyProvider = keyProvider;
        this.properties = properties;
    }

    public String issueAccessToken(Long userId, String email, List<SystemRole> roles) {
        JWTClaimsSet claims = baseClaims(userId, properties.accessTtl().getSeconds())
                .claim("email", email)
                .claim(CLAIM_ROLES, roles.stream().map(Enum::name).toList())
                .build();
        return sign(claims);
    }

    public String issueRefreshToken(Long userId) {
        JWTClaimsSet claims = baseClaims(userId, properties.refreshTtl().getSeconds())
                .claim(CLAIM_TYPE, TYPE_REFRESH)
                .build();
        return sign(claims);
    }

    public long accessTtlSeconds() {
        return properties.accessTtl().getSeconds();
    }

    private JWTClaimsSet.Builder baseClaims(Long userId, long ttlSeconds) {
        Instant now = Instant.now();
        return new JWTClaimsSet.Builder()
                .subject(String.valueOf(userId))
                .issuer(properties.issuer())
                .issueTime(Date.from(now))
                .expirationTime(Date.from(now.plusSeconds(ttlSeconds)))
                .jwtID(java.util.UUID.randomUUID().toString());
    }

    private String sign(JWTClaimsSet claims) {
        try {
            JWSHeader header = new JWSHeader.Builder(JWSAlgorithm.RS256)
                    .keyID(keyProvider.keyId())
                    .type(JOSEObjectType.JWT)
                    .build();
            SignedJWT jwt = new SignedJWT(header, claims);
            jwt.sign(new RSASSASigner(keyProvider.signingKey().toPrivateKey()));
            return jwt.serialize();
        } catch (Exception e) {
            throw new IllegalStateException("토큰 서명에 실패했다", e);
        }
    }
}
