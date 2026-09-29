package com.odh.chat.job;

import com.odh.chat.config.KafkaConfig;
import com.odh.common.Correlation;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.MDC;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * job 분산을 Kafka 에 맡긴다. jobKey 만 흘려보내고 질문/사용자 정보는 싣지 않는다 —
 * 워커가 Postgres 에서 {@link AnswerJobStore#findByKey}로 다시 읽으므로 페이로드 스키마를
 * 따로 진화시킬 필요가 없다.
 *
 * 상관관계 ID는 레코드 헤더로 함께 보낸다 — job 이 Kafka 를 거쳐
 * 완전히 다른 프로세스(worker)에서 처리되므로, 이 요청 스레드의 MDC 값은 헤더에 싣지
 * 않으면 워커 쪽에서 통째로 사라진다.
 */
@Component
public class AnswerJobProducer {

    private final KafkaTemplate<String, String> kafka;

    public AnswerJobProducer(KafkaTemplate<String, String> kafka) {
        this.kafka = kafka;
    }

    public void publish(UUID jobKey) {
        String value = jobKey.toString();
        ProducerRecord<String, String> record =
                new ProducerRecord<>(KafkaConfig.ANSWER_JOBS_TOPIC, value, value);
        String correlationId = MDC.get(Correlation.MDC_KEY);
        if (correlationId != null && !correlationId.isBlank()) {
            record.headers().add(Correlation.HEADER, correlationId.getBytes(StandardCharsets.UTF_8));
        }
        kafka.send(record);
    }
}
