package com.odh.chat.relay;

import com.odh.chat.web.RelayMode;
import com.odh.chat.web.SseProperties;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.RedisStandaloneConfiguration;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.listener.RedisMessageListenerContainer;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실제 Redis 로 발행(XADD → PUBLISH) → 알림 → XRANGE 경로가 유실 없이 동작하는지,
 * 그리고 이 경로가 새 TCP 연결을 만들지 않는지(블로킹 XREAD 가 연결을 새로 열어 임시 포트를 고갈시키는 원인) 본다.
 */
@Testcontainers
class AnswerRelayHubRedisIT {

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    private LettuceConnectionFactory factory;
    private StringRedisTemplate template;
    private RedisMessageListenerContainer container;
    private AnswerRelayHub hub;

    @BeforeEach
    void setUp() throws Exception {
        factory = new LettuceConnectionFactory(new RedisStandaloneConfiguration(redis.getHost(), redis.getMappedPort(6379)));
        factory.afterPropertiesSet();
        factory.start();
        template = new StringRedisTemplate(factory);
        RedisRelayConfiguration config = new RedisRelayConfiguration();
        container = config.relayListenerContainer(factory);
        container.afterPropertiesSet();
        container.start();
        hub = new AnswerRelayHub(config.relayChannels(container), config.relaySource(template),
                new SseProperties(Duration.ofMinutes(5), Duration.ofMillis(500), Duration.ofSeconds(5), RelayMode.PUBSUB, 100),
                new SimpleMeterRegistry());
    }

    @AfterEach
    void tearDown() throws Exception {
        hub.destroy();
        container.destroy();
        factory.destroy();
    }

    /** rag-service RedisAnswerSink._send 와 같은 순서 */
    private String publish(UUID job, String event, int seq) {
        String id = template.opsForStream().add(RelayKeys.stream(job), Map.of("event", event, "data", "{\"seq\":" + seq + "}")).getValue();
        template.convertAndSend(RelayKeys.channel(job), id);
        return id;
    }

    @Test
    void relaysEverythingInOrderIncludingRecordsWrittenBeforeOpen() throws Exception {
        UUID job = UUID.randomUUID();
        List<String> published = new ArrayList<>();
        published.add(publish(job, "sources", 0));   // 연결 전에 발행 — 첫 읽기가 따라잡아야 한다
        StreamSubscriberTest.RecordingOutput out = new StreamSubscriberTest.RecordingOutput();
        hub.open(job, RelayKeys.FROM_START, out).start();

        for (int i = 1; i <= 50; i++) {
            published.add(publish(job, "delta", i));
        }
        published.add(publish(job, "done", 51));

        assertThat(out.completed.await(10, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).containsExactlyElementsOf(published);
    }

    @Test
    void doesNotOpenNewConnectionsPerRead() throws Exception {
        UUID job = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput out = new StreamSubscriberTest.RecordingOutput();
        hub.open(job, RelayKeys.FROM_START, out).start();
        publish(job, "delta", 0);
        Thread.sleep(300);            // 구독·공유 커넥션이 다 열린 뒤부터 센다
        long before = totalConnections();

        for (int i = 1; i <= 200; i++) {
            publish(job, "delta", i);
        }
        publish(job, "done", 201);

        assertThat(out.completed.await(10, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).hasSize(202);
        // INFO 를 부르는 이 테스트의 연결 정도만 허용한다. POLL 모드는 읽기마다 1개씩 는다
        assertThat(totalConnections() - before).isLessThanOrEqualTo(2);
    }

    @Test
    void finishesEvenIfTheSubscriptionConnectionIsKilled() throws Exception {
        UUID job = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput out = new StreamSubscriberTest.RecordingOutput();
        hub.open(job, RelayKeys.FROM_START, out).start();
        List<String> published = new ArrayList<>();
        published.add(publish(job, "delta", 0));
        Thread.sleep(300);

        redis.execInContainer("redis-cli", "CLIENT", "KILL", "TYPE", "pubsub");
        for (int i = 1; i <= 10; i++) {
            published.add(publish(job, "delta", i));  // 재연결 전이면 알림은 사라진다
        }
        published.add(publish(job, "done", 11));

        assertThat(out.completed.await(10, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).containsExactlyElementsOf(published);
    }

    /**
     * Spring Data Redis 컨테이너는 구독 연결을 준비하는 중에 들어온 채널을 구독하지 않는다
     * (RedisRelayConfiguration.ContainerRelayChannels 주석). 앵커가 없으면 동시에 200개를 열 때
     * 22개가 구독되지 않아 하트비트 주기 확인(15s)으로만 전달된다. 갓 만든(아직 구독 중이 아닌) 컨테이너에
     * 동시에 열고, 하트비트를 60초로 둬 주기 확인이 구제하지 못하게 한다.
     */
    @Test
    void manyJobsOpenedConcurrentlyOnAFreshContainerAreAllSubscribed() throws Exception {
        SimpleMeterRegistry meters = new SimpleMeterRegistry();
        AnswerRelayHub slowTickHub = hub(container, meters, Duration.ofSeconds(60));
        int n = 200;
        List<UUID> jobs = new ArrayList<>();
        List<StreamSubscriberTest.RecordingOutput> outs = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            jobs.add(UUID.randomUUID());
            outs.add(new StreamSubscriberTest.RecordingOutput());
        }
        try (var pool = java.util.concurrent.Executors.newVirtualThreadPerTaskExecutor()) {
            for (int i = 0; i < n; i++) {
                int k = i;
                pool.submit(() -> slowTickHub.open(jobs.get(k), RelayKeys.FROM_START, outs.get(k)).start());
            }
        }

        // open 은 구독 확정 뒤에 돌아온다 — 기다리지 않고 바로 센다. 앵커 1 + job 200
        assertThat(subscribedChannels()).isEqualTo(n + 1);

        for (UUID job : jobs) {
            publish(job, "delta", 0);
            publish(job, "done", 1);
        }
        for (StreamSubscriberTest.RecordingOutput out : outs) {
            assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
            assertThat(out.ids).hasSize(2);
        }
        assertThat(meters.get("relay.wake.missed").counter().count()).isZero();
        slowTickHub.destroy();
    }

    /** 인스턴스 둘: 알림은 그 job 을 연 인스턴스에만 간다(패턴 구독이면 서로의 알림을 받아 버린다). */
    @Test
    void notificationsGoOnlyToTheInstanceHoldingTheJob() throws Exception {
        RedisRelayConfiguration config = new RedisRelayConfiguration();
        RedisMessageListenerContainer otherContainer = config.relayListenerContainer(factory);
        otherContainer.afterPropertiesSet();
        otherContainer.start();
        SimpleMeterRegistry metersA = new SimpleMeterRegistry();
        SimpleMeterRegistry metersB = new SimpleMeterRegistry();
        AnswerRelayHub a = hub(container, metersA, Duration.ofSeconds(60));
        AnswerRelayHub b = hub(otherContainer, metersB, Duration.ofSeconds(60));
        UUID jobA = UUID.randomUUID();
        UUID jobB = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput outA = new StreamSubscriberTest.RecordingOutput();
        StreamSubscriberTest.RecordingOutput outB = new StreamSubscriberTest.RecordingOutput();
        a.open(jobA, RelayKeys.FROM_START, outA).start();
        b.open(jobB, RelayKeys.FROM_START, outB).start();

        for (int i = 0; i < 50; i++) {
            publish(jobA, "delta", i);
            publish(jobB, "delta", i);
        }
        publish(jobA, "done", 50);
        publish(jobB, "done", 50);

        assertThat(outA.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(outB.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(outA.ids).hasSize(51);
        assertThat(outB.ids).hasSize(51);
        assertThat(metersA.get("relay.notifications.unrouted").counter().count()).isZero();
        assertThat(metersB.get("relay.notifications.unrouted").counter().count()).isZero();
        a.destroy();
        b.destroy();
        otherContainer.destroy();
    }

    private AnswerRelayHub hub(RedisMessageListenerContainer c, SimpleMeterRegistry meters, Duration heartbeat) {
        RedisRelayConfiguration config = new RedisRelayConfiguration();
        return new AnswerRelayHub(config.relayChannels(c), config.relaySource(template),
                new SseProperties(Duration.ofMinutes(5), heartbeat, Duration.ofSeconds(5), RelayMode.PUBSUB, 100),
                meters);
    }

    private int subscribedChannels() throws Exception {
        String out = redis.execInContainer("redis-cli", "PUBSUB", "CHANNELS", "chat:answer:live:*").getStdout();
        return (int) out.lines().filter(line -> !line.isBlank()).count();
    }

    private long totalConnections() {
        Properties stats = template.execute((org.springframework.data.redis.core.RedisCallback<Properties>)
                connection -> connection.serverCommands().info("stats"));
        return Long.parseLong(stats.getProperty("total_connections_received"));
    }
}
