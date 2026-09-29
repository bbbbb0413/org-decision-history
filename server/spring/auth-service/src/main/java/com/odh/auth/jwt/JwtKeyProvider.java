package com.odh.auth.jwt;

import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.RSAPublicKeySpec;
import java.util.Base64;
import java.util.Map;

/**
 * RS256 서명 키를 보관하고 공개키를 JWKS 로 제공한다.
 *
 * 게이트웨이는 이 JWKS 를 받아 토큰 서명을 검증한다. 대칭키를 쓰지 않는 이유는
 * 서명 권한을 auth-service 한 곳에 두고 나머지 서비스에는 검증 권한만 주기 위해서다.
 */
@Component
public class JwtKeyProvider {

    private static final Logger log = LoggerFactory.getLogger(JwtKeyProvider.class);
    private static final int KEY_SIZE = 2048;

    private final RSAKey rsaKey;

    public JwtKeyProvider(JwtProperties properties) {
        this.rsaKey = properties.privateKey() == null || properties.privateKey().isBlank()
                ? generateEphemeralKey()
                : loadKey(properties.privateKey());
    }

    /** 서명에 사용할 개인키를 포함한 JWK */
    public RSAKey signingKey() {
        return rsaKey;
    }

    public String keyId() {
        return rsaKey.getKeyID();
    }

    /** 공개키만 담은 JWKS. /.well-known/jwks.json 응답 본문이다. */
    public Map<String, Object> publicJwkSet() {
        return new JWKSet(rsaKey.toPublicJWK()).toJSONObject();
    }

    private RSAKey generateEphemeralKey() {
        log.warn("auth.jwt.private-key 가 비어 있어 임시 RSA 키를 생성했다. "
                + "재시작하면 키가 바뀌어 기존 토큰이 무효가 된다. 로컬 개발에서만 쓸 것.");
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
            generator.initialize(KEY_SIZE);
            KeyPair pair = generator.generateKeyPair();
            return new RSAKey.Builder((RSAPublicKey) pair.getPublic())
                    .privateKey(pair.getPrivate())
                    .keyUse(KeyUse.SIGNATURE)
                    .keyID("dev-" + System.currentTimeMillis())
                    .build();
        } catch (Exception e) {
            throw new IllegalStateException("RSA 키 생성에 실패했다", e);
        }
    }

    private RSAKey loadKey(String pem) {
        try {
            String body = pem.replaceAll("-----[A-Z ]+-----", "").replaceAll("\\s", "");
            byte[] der = Base64.getDecoder().decode(body);
            KeyFactory factory = KeyFactory.getInstance("RSA");
            RSAPrivateKey privateKey = (RSAPrivateKey) factory.generatePrivate(new PKCS8EncodedKeySpec(der));

            // PKCS#8 개인키에서 공개 지수를 복원한다. 표준 RSA 공개 지수 65537 을 전제한다.
            RSAPublicKey publicKey = (RSAPublicKey) factory.generatePublic(
                    new RSAPublicKeySpec(privateKey.getModulus(), java.math.BigInteger.valueOf(65537)));

            return new RSAKey.Builder(publicKey)
                    .privateKey(privateKey)
                    .keyUse(KeyUse.SIGNATURE)
                    .keyID("primary")
                    .build();
        } catch (Exception e) {
            throw new IllegalStateException("auth.jwt.private-key 를 읽을 수 없다. PKCS#8 PEM 인지 확인할 것", e);
        }
    }
}
