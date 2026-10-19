package com.odh.document.config;

import com.odh.common.internal.InternalClientsConfiguration;
import com.odh.common.servlet.ServiceFilterConfiguration;
import com.odh.common.web.CommonExceptionHandler;
import com.odh.document.service.OutboxProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;

@Configuration
@EnableScheduling
@EnableAsync
@EnableConfigurationProperties(OutboxProperties.class)
@Import({ServiceFilterConfiguration.class, InternalClientsConfiguration.class, CommonExceptionHandler.class})
public class DocumentServiceConfiguration {

    /**
     * 일괄 가져오기 처리 전용 스레드 풀.
     * 기본 SimpleAsyncTaskExecutor 는 스레드를 무제한으로 만들어 동시에 큰 배치가
     * 여러 건 들어오면 자원을 소진한다. 큐가 차면 호출자가 직접 처리해 요청을 잃지 않는다.
     */
    @Bean("importExecutor")
    public Executor importExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(4);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("import-");
        executor.setRejectedExecutionHandler(new java.util.concurrent.ThreadPoolExecutor.CallerRunsPolicy());
        executor.initialize();
        return executor;
    }
}
