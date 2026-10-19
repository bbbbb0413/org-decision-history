package com.odh.document.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.odh.document.domain.OutboxEventType;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 발행하는 스트림 이름과 이벤트 종류가 계약(contracts/events/document-events.schema.json)과 같은지 본다.
 * 소비자(Python)도 같은 계약 파일과 맞춰 본다(server/python/shared/tests/test_events.py) — 이름이 한쪽에서만
 * 바뀌면 오류 없이 이벤트가 사라지므로 둘 다 계약에 묶는다.
 */
class OutboxPublisherContractTest {

    private static final JsonNode SCHEMA = readSchema();

    @Test
    void streamKeyMatchesContract() {
        assertThat(OutboxPublisher.STREAM_KEY).isEqualTo(SCHEMA.get("x-stream").asText());
    }

    @Test
    void eventTypesMatchContract() {
        Set<String> contract = new HashSet<>();
        SCHEMA.at("/properties/eventType/enum").forEach(node -> contract.add(node.asText()));

        assertThat(Arrays.stream(OutboxEventType.values()).map(Enum::name)).containsExactlyInAnyOrderElementsOf(contract);
    }

    private static JsonNode readSchema() {
        Path dir = Path.of("").toAbsolutePath();
        while (dir != null && !Files.isDirectory(dir.resolve("contracts"))) {
            dir = dir.getParent();
        }
        if (dir == null) {
            throw new IllegalStateException("contracts 디렉터리를 찾지 못했다. 작업 디렉터리: " + Path.of("").toAbsolutePath());
        }
        try {
            return new ObjectMapper().readTree(dir.resolve("contracts/events/document-events.schema.json").toFile());
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }
}
