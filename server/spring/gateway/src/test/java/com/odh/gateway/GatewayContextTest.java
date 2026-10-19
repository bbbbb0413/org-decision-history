package com.odh.gateway;

import com.odh.gateway.filter.JwtVerifier;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 게이트웨이 컨텍스트가 실제로 뜨는지. 단위 테스트는 빈을 직접 만들어 배선 실수를 못 잡는다 —
 * 예를 들어 JwtVerifier 에 생성자가 둘이면 Spring 이 생성자를 고르지 못해 기동이 실패한다.
 * Redis·auth-service 는 처음 쓸 때 연결하므로 띄우지 않아도 된다.
 */
@SpringBootTest(properties = {"server.port=0", "management.server.port=0"})
class GatewayContextTest {

    @Autowired
    JwtVerifier verifier;

    @Test
    void contextStarts() {
        assertThat(verifier).isNotNull();
    }
}
