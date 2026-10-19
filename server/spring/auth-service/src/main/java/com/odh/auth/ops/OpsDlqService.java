package com.odh.auth.ops;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.Limit;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.RedisOperations;
import org.springframework.data.redis.core.SessionCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * 스트림 DLQ 조회·재처리.
 *
 * <p>컨슈머({@code history_shared.streams.RedisStreamConsumer})는 전달 한도를 넘긴 항목과
 * 영구 실패 항목을 {@code {stream}:dlq} 로 옮기고 원본을 ACK 한다. 그때까지는 그 뒤가
 * 없어서, 색인이나 상충 감지가 조용히 유실돼도 아무도 몰랐다. 이 서비스가 건수·항목을
 * 보여주고 원본 스트림으로 되돌린다.
 *
 * <p>대상 스트림은 {@link Target} 허용 목록으로 고정한다 — 요청이 임의의 Redis 키를
 * 읽거나 임의의 스트림에 쓰게 만들면 안 된다. 항목에 실려 온 {@code dlq.sourceStream} 도
 * 이 목록과 맞는지 확인한 뒤에만 되돌린다.
 *
 * <p>스트림은 auth-service 가 이미 토큰 폐기 워터마크로 쓰고 있는 것과 같은 Redis
 * 인스턴스·DB(0번)에 있다. 워커들도 DB 를 지정하지 않아 같은 0번을 쓴다.
 */
@Service
public class OpsDlqService {

    private static final Logger log = LoggerFactory.getLogger(OpsDlqService.class);

    /** 한 번에 되돌리거나 버릴 수 있는 항목 수. */
    static final int MAX_BATCH = 50;
    private static final String FIELD_PREFIX = "dlq.";

    /**
     * 조회·재처리를 허용하는 스트림. 이름의 원본은 {@code history_shared.events}
     * (STREAM_DOCUMENT_EVENTS·STREAM_INDEX_EVENTS)와 document-service
     * {@code OutboxPublisher.STREAM_KEY} 이고, {@code contracts/README.md} 에 문서화돼 있다.
     */
    public enum Target {
        DOCUMENT_EVENTS("document-events", "stream:document-events", "index-worker"),
        INDEX_EVENTS("index-events", "stream:index-events", "governance-worker");

        private final String key;
        private final String stream;
        private final String consumerService;

        Target(String key, String stream, String consumerService) {
            this.key = key;
            this.stream = stream;
            this.consumerService = consumerService;
        }

        public String key() {
            return key;
        }

        public String stream() {
            return stream;
        }

        public String dlqStream() {
            return stream + ":dlq";
        }

        /** 이 스트림을 소비하다 실패한 서비스. 화면에서 어디를 봐야 하는지 알려준다. */
        public String consumerService() {
            return consumerService;
        }

        public static Optional<Target> ofKey(String key) {
            for (Target t : values()) {
                if (t.key.equals(key)) {
                    return Optional.of(t);
                }
            }
            return Optional.empty();
        }

        static Optional<Target> ofStream(String stream) {
            for (Target t : values()) {
                if (t.stream.equals(stream)) {
                    return Optional.of(t);
                }
            }
            return Optional.empty();
        }
    }

    private final StringRedisTemplate redis;
    /** 목록 한 번에 읽어올 최대 항목 수(ops.dlq-max-entries) */
    private final int maxEntries;

    public OpsDlqService(StringRedisTemplate redis, OpsProperties props) {
        this.redis = redis;
        this.maxEntries = props.dlqMaxEntries();
    }

    /** 스트림별 DLQ 건수와 가장 오래된 항목 시각. Redis 에 접근하지 못해도 화면이 비지 않게 0 으로 내려간다. */
    public List<OpsDtos.DlqStatus> statuses() {
        List<OpsDtos.DlqStatus> result = new ArrayList<>();
        for (Target target : Target.values()) {
            result.add(statusOf(target));
        }
        return result;
    }

    private OpsDtos.DlqStatus statusOf(Target target) {
        try {
            Long size = redis.opsForStream().size(target.dlqStream());
            long count = size == null ? 0 : size;
            Instant oldest = count == 0 ? null : firstEntryTime(target.dlqStream());
            return new OpsDtos.DlqStatus(target.key(), target.stream(), target.dlqStream(),
                    target.consumerService(), count, oldest);
        } catch (Exception e) {
            log.warn("DLQ 건수 조회 실패: {} - {}", target.dlqStream(), e.getMessage());
            return new OpsDtos.DlqStatus(target.key(), target.stream(), target.dlqStream(),
                    target.consumerService(), 0, null);
        }
    }

    private Instant firstEntryTime(String dlqStream) {
        List<MapRecord<String, Object, Object>> first =
                redis.opsForStream().range(dlqStream, Range.unbounded(), Limit.limit().count(1));
        if (first == null || first.isEmpty()) {
            return null;
        }
        return timestampOf(first.get(0).getId());
    }

    /** 최근 항목부터 {@code limit} 건. */
    public List<OpsDtos.DlqEntry> entries(Target target, int limit) {
        int capped = Math.max(1, Math.min(limit, maxEntries));
        try {
            List<MapRecord<String, Object, Object>> records = redis.opsForStream()
                    .reverseRange(target.dlqStream(), Range.unbounded(), Limit.limit().count(capped));
            if (records == null) {
                return List.of();
            }
            List<OpsDtos.DlqEntry> entries = new ArrayList<>(records.size());
            for (MapRecord<String, Object, Object> record : records) {
                entries.add(toEntry(target, record));
            }
            return entries;
        } catch (Exception e) {
            log.warn("DLQ 항목 조회 실패: {} - {}", target.dlqStream(), e.getMessage());
            return List.of();
        }
    }

    private OpsDtos.DlqEntry toEntry(Target target, MapRecord<String, Object, Object> record) {
        Map<String, String> fields = asStringMap(record.getValue());
        return new OpsDtos.DlqEntry(
                record.getId().getValue(),
                target.key(),
                fields.getOrDefault("dlq.sourceStream", target.stream()),
                fields.get("dlq.sourceId"),
                fields.get("dlq.group"),
                parseInt(fields.get("dlq.deliveries")),
                fields.get("dlq.reason"),
                timestampOf(record.getId()),
                payloadOf(fields));
    }

    /** {@code dlq.*} 메타 필드를 뺀 원래 이벤트 본문. */
    private static Map<String, String> payloadOf(Map<String, String> fields) {
        Map<String, String> payload = new LinkedHashMap<>();
        fields.forEach((k, v) -> {
            if (!k.startsWith(FIELD_PREFIX)) {
                payload.put(k, v);
            }
        });
        return payload;
    }

    /**
     * 항목을 원본 스트림으로 되돌린다. {@code dlq.*} 메타 필드는 떼고 원래 본문만 다시 넣어,
     * 컨슈머가 처음 받는 것과 같은 모양이 되게 한다(전달 횟수도 1 부터 다시 센다).
     *
     * <p>XADD 와 DLQ 에서의 XDEL 을 한 트랜잭션으로 묶는다 — 사이에 끊기면 항목이 사라지거나
     * 두 번 들어간다. 컨슈머 쪽 DLQ 적재도 같은 이유로 파이프라인을 쓴다.
     *
     * <p>이미 없는 id(다른 관리자가 먼저 처리)는 실패로 보고하고 넘어간다. 되돌린 뒤 같은
     * 이유로 또 실패하면 다시 DLQ 로 온다 — 무한 루프가 아니라 재시도 한 판이다.
     */
    public OpsDtos.DlqActionResult replay(Target target, List<String> ids) {
        List<String> capped = capBatch(ids);
        List<String> failed = new ArrayList<>();
        int done = 0;
        for (String id : capped) {
            if (replayOne(target, id)) {
                done++;
            } else {
                failed.add(id);
            }
        }
        log.info("DLQ 재처리: stream={} 요청={} 성공={} 실패={}", target.dlqStream(), capped.size(), done, failed.size());
        return new OpsDtos.DlqActionResult(done, failed);
    }

    private boolean replayOne(Target target, String id) {
        try {
            List<MapRecord<String, Object, Object>> found =
                    redis.opsForStream().range(target.dlqStream(), Range.closed(id, id));
            if (found == null || found.isEmpty()) {
                return false;
            }
            Map<String, String> fields = asStringMap(found.get(0).getValue());

            // 항목에 실려 온 원본 스트림 이름도 허용 목록과 맞는지 확인한다. 워커만 쓰는
            // 필드지만, 여기서 임의 키에 XADD 하는 경로를 만들어 두지 않는다.
            String sourceStream = fields.get("dlq.sourceStream");
            String destination = sourceStream == null ? target.stream()
                    : Target.ofStream(sourceStream).map(Target::stream).orElse(null);
            if (destination == null) {
                log.warn("DLQ 항목의 원본 스트림이 허용 목록에 없어 되돌리지 않는다: {}", sourceStream);
                return false;
            }

            Map<String, String> body = payloadOf(fields);
            if (body.isEmpty()) {
                log.warn("DLQ 항목에 본문 필드가 없어 되돌리지 않는다: {} {}", target.dlqStream(), id);
                return false;
            }

            String finalDestination = destination;
            redis.execute(new SessionCallback<Object>() {
                @Override
                @SuppressWarnings("unchecked")
                public <K, V> Object execute(RedisOperations<K, V> operations) {
                    StringRedisTemplate ops = (StringRedisTemplate) operations;
                    ops.multi();
                    ops.opsForStream().add(StreamRecords.mapBacked(body).withStreamKey(finalDestination));
                    ops.opsForStream().delete(target.dlqStream(), id);
                    return ops.exec();
                }
            });
            return true;
        } catch (Exception e) {
            log.warn("DLQ 항목 재처리 실패: {} {} - {}", target.dlqStream(), id, e.getMessage());
            return false;
        }
    }

    /**
     * 되돌릴 수 없는 항목을 버린다. 이게 없으면 영구 실패 항목이 DLQ 에 영원히 남아
     * 건수가 경보로서 쓸모를 잃는다.
     */
    public OpsDtos.DlqActionResult discard(Target target, List<String> ids) {
        List<String> capped = capBatch(ids);
        List<String> failed = new ArrayList<>();
        int done = 0;
        for (String id : capped) {
            try {
                Long removed = redis.opsForStream().delete(target.dlqStream(), id);
                if (removed != null && removed > 0) {
                    done++;
                } else {
                    failed.add(id);
                }
            } catch (Exception e) {
                log.warn("DLQ 항목 삭제 실패: {} {} - {}", target.dlqStream(), id, e.getMessage());
                failed.add(id);
            }
        }
        log.info("DLQ 폐기: stream={} 요청={} 성공={} 실패={}", target.dlqStream(), capped.size(), done, failed.size());
        return new OpsDtos.DlqActionResult(done, failed);
    }

    private List<String> capBatch(List<String> ids) {
        if (ids == null || ids.isEmpty()) {
            return List.of();
        }
        return ids.size() <= MAX_BATCH ? List.copyOf(ids) : List.copyOf(ids.subList(0, MAX_BATCH));
    }

    private static Map<String, String> asStringMap(Map<Object, Object> raw) {
        Map<String, String> out = new LinkedHashMap<>();
        raw.forEach((k, v) -> out.put(String.valueOf(k), v == null ? null : String.valueOf(v)));
        return out;
    }

    /** 스트림 id 의 앞부분이 밀리초 타임스탬프다(`1726900000000-0`). */
    private static Instant timestampOf(RecordId id) {
        String value = id.getValue();
        int dash = value.indexOf('-');
        try {
            return Instant.ofEpochMilli(Long.parseLong(dash < 0 ? value : value.substring(0, dash)));
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static int parseInt(String value) {
        try {
            return value == null ? 0 : Integer.parseInt(value);
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
