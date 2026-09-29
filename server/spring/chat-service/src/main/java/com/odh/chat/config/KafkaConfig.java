package com.odh.chat.config;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/**
 * 답변 생성 job 을 워커 여러 대로 나눠 분산시키는 토픽.
 *
 * job 소비·재시도·DLT 처리는 rag-service(Python/LangGraph)가 한다 — chat-service 에는
 * {@code @KafkaListener}가 없다. 발행하는 쪽이 토픽을 선언한다
 * ({@link com.odh.chat.job.AnswerJobProducer}가 발행).
 */
@Configuration
public class KafkaConfig {

    public static final String ANSWER_JOBS_TOPIC = "chat.answer.jobs";
    /** {@link DeadLetterPublishingRecoverer} 의 기본 명명 규칙(".DLT" 접미사)을 그대로 쓴다. */
    public static final String ANSWER_JOBS_DLT_TOPIC = ANSWER_JOBS_TOPIC + ".DLT";

    @Bean
    public NewTopic chatAnswerJobsTopic() {
        // 파티션 수만큼 워커가 동시에 job 을 나눠 처리할 수 있다.
        return TopicBuilder.name(ANSWER_JOBS_TOPIC).partitions(6).replicas(1).build();
    }

    /**
     * DLT 도 원본과 같은 파티션 수로 만들어 둔다. DeadLetterPublishingRecoverer 가 기본적으로
     * 원본 레코드와 같은 파티션 번호로 보내는데, DLT 파티션 수가 더 적으면 그 매핑이 깨진다.
     */
    @Bean
    public NewTopic chatAnswerJobsDeadLetterTopic() {
        return TopicBuilder.name(ANSWER_JOBS_DLT_TOPIC).partitions(6).replicas(1).build();
    }
}
