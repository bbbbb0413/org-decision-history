package com.odh.chat.relay;

import com.odh.chat.web.RelayMode;
import com.odh.chat.web.SseProperties;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/** 채널 구독 참조 카운트, 알림이 그 job 의 구독자에게만 가는지, 해제·재구독 순서. */
class AnswerRelayHubTest {

    static final class FakeChannels implements RelayChannels {
        final List<String> log = new CopyOnWriteArrayList<>();
        final Map<String, Runnable> handlers = new ConcurrentHashMap<>();

        @Override
        public void subscribe(String channel, Runnable onMessage) {
            log.add("+" + channel);
            handlers.put(channel, onMessage);
        }

        @Override
        public void unsubscribe(String channel) {
            log.add("-" + channel);
            handlers.remove(channel);
        }

        void publish(UUID job) {
            Runnable h = handlers.get(RelayKeys.channel(job));
            if (h != null) {
                h.run();
            }
        }
    }

    private final FakeChannels channels = new FakeChannels();
    private final StreamSubscriberTest.MemoryStream stream = new StreamSubscriberTest.MemoryStream();
    private final SimpleMeterRegistry meters = new SimpleMeterRegistry();
    private final AnswerRelayHub hub = new AnswerRelayHub(channels, stream,
            new SseProperties(Duration.ofMinutes(5), Duration.ofSeconds(30), Duration.ofSeconds(5), RelayMode.PUBSUB, 100),
            meters);

    @Test
    void subscribesOncePerJobAndUnsubscribesWhenLastLeaves() throws Exception {
        UUID job = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput a = new StreamSubscriberTest.RecordingOutput();
        StreamSubscriberTest.RecordingOutput b = new StreamSubscriberTest.RecordingOutput();

        StreamSubscriber sa = hub.open(job, RelayKeys.FROM_START, a);
        StreamSubscriber sb = hub.open(job, RelayKeys.FROM_START, b);
        sa.start();
        sb.start();
        assertThat(channels.log).containsExactly("+" + RelayKeys.channel(job));
        assertThat(hub.activeCount()).isEqualTo(2);

        sa.close();
        assertThat(a.completed.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(channels.log).hasSize(1);

        sb.close();
        assertThat(b.completed.await(2, TimeUnit.SECONDS)).isTrue();
        waitUntil(() -> channels.log.size() == 2);
        assertThat(channels.log).containsExactly("+" + RelayKeys.channel(job), "-" + RelayKeys.channel(job));
        assertThat(hub.activeCount()).isZero();
    }

    @Test
    void notificationWakesOnlyThatJobsSubscribersIncludingTwoTabs() throws Exception {
        UUID job = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput a = new StreamSubscriberTest.RecordingOutput();
        StreamSubscriberTest.RecordingOutput b = new StreamSubscriberTest.RecordingOutput();
        StreamSubscriberTest.RecordingOutput c = new StreamSubscriberTest.RecordingOutput();
        hub.open(job, RelayKeys.FROM_START, a).start();
        hub.open(job, RelayKeys.FROM_START, b).start();
        hub.open(UUID.randomUUID(), RelayKeys.FROM_START, c).start();
        Thread.sleep(100);            // 첫 읽기(빈 Stream)를 마칠 때까지

        stream.add("delta");
        stream.add("done");
        channels.publish(job);

        assertThat(a.completed.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(b.completed.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(a.ids).containsExactly("1-1", "1-2");
        assertThat(b.ids).containsExactly("1-1", "1-2");
        // 다른 job 은 같은 메모리 Stream 을 보지만 알림이 가지 않았으므로 하트비트(30s) 전까지 읽지 않는다
        assertThat(c.ids).isEmpty();
    }

    @Test
    void reopeningAfterTheLastLeftSubscribesAgainAfterTheUnsubscribe() throws Exception {
        UUID job = UUID.randomUUID();
        StreamSubscriberTest.RecordingOutput a = new StreamSubscriberTest.RecordingOutput();
        StreamSubscriber sa = hub.open(job, RelayKeys.FROM_START, a);
        sa.start();
        sa.close();
        assertThat(a.completed.await(2, TimeUnit.SECONDS)).isTrue();
        waitUntil(() -> channels.log.size() == 2);

        hub.open(job, RelayKeys.FROM_START, new StreamSubscriberTest.RecordingOutput()).start();

        assertThat(channels.log).containsExactly(
                "+" + RelayKeys.channel(job), "-" + RelayKeys.channel(job), "+" + RelayKeys.channel(job));
    }

    @Test
    void notificationForAJobWithNoLocalSubscriberIsCountedAsUnrouted() {
        UUID job = UUID.randomUUID();
        Runnable[] handler = new Runnable[1];
        AnswerRelayHub capturing = new AnswerRelayHub(new RelayChannels() {
            @Override
            public void subscribe(String channel, Runnable onMessage) {
                handler[0] = onMessage;
            }

            @Override
            public void unsubscribe(String channel) {
            }
        }, stream, new SseProperties(Duration.ofMinutes(5), Duration.ofSeconds(30), Duration.ofSeconds(5), RelayMode.PUBSUB, 100),
                meters);
        StreamSubscriber s = capturing.open(job, RelayKeys.FROM_START, new StreamSubscriberTest.RecordingOutput());
        s.close();
        s.start();                    // 바로 끝나며 해제된다

        await(() -> capturing.activeCount() == 0);
        handler[0].run();             // 해제 직후 도착한 알림

        assertThat(meters.get("relay.notifications.unrouted").counter().count()).isEqualTo(1.0);
    }

    private static void await(java.util.function.BooleanSupplier condition) {
        long deadline = System.currentTimeMillis() + 2000;
        while (!condition.getAsBoolean() && System.currentTimeMillis() < deadline) {
            Thread.onSpinWait();
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }

    private static void waitUntil(java.util.function.BooleanSupplier condition) throws InterruptedException {
        CountDownLatch never = new CountDownLatch(1);
        for (int i = 0; i < 100 && !condition.getAsBoolean(); i++) {
            never.await(20, TimeUnit.MILLISECONDS);
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }
}
