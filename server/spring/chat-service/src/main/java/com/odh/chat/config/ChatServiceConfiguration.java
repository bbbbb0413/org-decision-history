package com.odh.chat.config;

import com.odh.common.internal.InternalClientsConfiguration;
import com.odh.common.servlet.ServiceFilterConfiguration;
import com.odh.common.web.CommonExceptionHandler;
import com.odh.chat.timeline.TimelineProperties;
import com.odh.chat.web.SseProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

/**
 * common 은 각 서비스의 컴포넌트 스캔 대상이 아니므로 자동으로 잡히지 않는다.
 * 게이트웨이가 이 설정을 집어가지 않게 하려는 의도이기도 하다.
 *
 * 임베딩·벡터 검색은 rag-service(Python)가 하므로 chat-service 에는 그 설정이 없다.
 */
@Configuration
@Import({ServiceFilterConfiguration.class, InternalClientsConfiguration.class, CommonExceptionHandler.class})
@EnableConfigurationProperties({SseProperties.class, TimelineProperties.class})
public class ChatServiceConfiguration {
}
