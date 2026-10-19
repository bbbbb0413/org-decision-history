package com.odh.chat.relay;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 알림은 깨우기만 하고 데이터는 Stream 에서 "마지막으로 보낸 것 다음"부터 읽는다는 규칙이
 * 유실·중복·순서 문제를 만들지 않는지 본다. Redis 대신 메모리 Stream 을 쓴다.
 */
class StreamSubscriberTest {

    private final UUID job = UUID.randomUUID();

    /** 메모리 Stream. ID 는 "1-순번" */
    static final class MemoryStream implements RelaySource {
        final List<RelayRecord> records = Collections.synchronizedList(new ArrayList<>());
        final AtomicInteger reads = new AtomicInteger();

        void add(String event) {
            records.add(new RelayRecord("1-" + (records.size() + 1), event, "{}"));
        }

        @Override
        public List<RelayRecord> after(String streamKey, String afterId, int count) {
            reads.incrementAndGet();
            long after = Long.parseLong(afterId.substring(afterId.indexOf('-') + 1));
            List<RelayRecord> out = new ArrayList<>();
            synchronized (records) {
                for (RelayRecord r : records) {
                    long seq = Long.parseLong(r.id().substring(2));
                    if (seq > after && out.size() < count) {
                        out.add(r);
                    }
                }
            }
            return out;
        }
    }

    static final class RecordingOutput implements RelayOutput {
        final List<String> ids = new CopyOnWriteArrayList<>();
        final AtomicInteger heartbeats = new AtomicInteger();
        final CountDownLatch completed = new CountDownLatch(1);
        volatile boolean failOnSend;

        @Override
        public void event(RelayRecord record) throws IOException {
            if (failOnSend) {
                throw new IOException("client gone");
            }
            ids.add(record.id());
        }

        @Override
        public void heartbeat() throws IOException {
            heartbeats.incrementAndGet();
        }

        @Override
        public void complete() {
            completed.countDown();
        }
    }

    private StreamSubscriber subscriber(MemoryStream stream, RecordingOutput out, String startAfter,
                                        Duration heartbeat, AtomicInteger tickCatches, CountDownLatch closed) {
        return new StreamSubscriber(job, startAfter, stream, out, heartbeat, 3,
                tickCatches::incrementAndGet, closed::countDown);
    }

    @Test
    void catchesUpOnStartThenFollowsWakesUntilDone() throws Exception {
        MemoryStream stream = new MemoryStream();
        stream.add("sources");
        stream.add("delta");          // 연결 전에 이미 쌓인 것
        RecordingOutput out = new RecordingOutput();
        CountDownLatch closed = new CountDownLatch(1);
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofSeconds(30), new AtomicInteger(), closed);
        s.start();

        for (int i = 0; i < 5; i++) {
            stream.add("delta");
            s.wake();
        }
        stream.add("done");
        s.wake();

        assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(closed.await(1, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).containsExactly("1-1", "1-2", "1-3", "1-4", "1-5", "1-6", "1-7", "1-8");
    }

    @Test
    void resumesAfterLastEventIdWithoutDuplicates() throws Exception {
        MemoryStream stream = new MemoryStream();
        for (int i = 0; i < 4; i++) {
            stream.add("delta");
        }
        stream.add("done");
        RecordingOutput out = new RecordingOutput();
        StreamSubscriber s = subscriber(stream, out, "1-2", Duration.ofSeconds(30), new AtomicInteger(), new CountDownLatch(1));
        s.start();

        assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).containsExactly("1-3", "1-4", "1-5");
    }

    /** 반례: 중간 알림이 사라지고 뒤 알림이 먼저 와도, 읽기가 lastId 다음부터라 빠지지 않는다. */
    @Test
    void lostWakesInTheMiddleAreCoveredByTheNextWake() throws Exception {
        MemoryStream stream = new MemoryStream();
        RecordingOutput out = new RecordingOutput();
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofSeconds(30), new AtomicInteger(), new CountDownLatch(1));
        s.start();

        for (int i = 0; i < 7; i++) {
            stream.add("delta");      // 알림 없음 = 유실
        }
        stream.add("delta");
        s.wake();                     // 마지막 하나만 도착
        stream.add("done");
        s.wake();

        assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).hasSize(9).doesNotHaveDuplicates();
        assertThat(out.ids.get(8)).isEqualTo("1-9");
    }

    /** 마지막 done 알림이 사라지면 뒤따르는 알림이 없다 — 하트비트 주기 확인이 읽어야 한다. */
    @Test
    void lostFinalWakeIsCaughtByHeartbeatTick() throws Exception {
        MemoryStream stream = new MemoryStream();
        RecordingOutput out = new RecordingOutput();
        AtomicInteger tickCatches = new AtomicInteger();
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofMillis(100), tickCatches, new CountDownLatch(1));
        s.start();

        Thread.sleep(50);
        stream.add("done");           // 알림 없음

        assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).containsExactly("1-1");
        assertThat(out.heartbeats.get()).isGreaterThanOrEqualTo(1);
        assertThat(tickCatches.get()).isEqualTo(1);
    }

    @Test
    void manyWakesCoalesceIntoFewReads() throws Exception {
        MemoryStream stream = new MemoryStream();
        RecordingOutput out = new RecordingOutput();
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofSeconds(30), new AtomicInteger(), new CountDownLatch(1));

        for (int i = 0; i < 200; i++) {
            stream.add("delta");
            s.wake();                 // 시작 전에 몰린 알림
        }
        stream.add("done");
        s.wake();
        s.start();

        assertThat(out.completed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(out.ids).hasSize(201).doesNotHaveDuplicates();
        // 배치 3 으로 201개 = 최소 68번 읽기. 알림 201번마다 따로 읽었다면 그보다 훨씬 많다
        assertThat(stream.reads.get()).isLessThan(100);
    }

    @Test
    void clientDisconnectStopsAndReleases() throws Exception {
        MemoryStream stream = new MemoryStream();
        RecordingOutput out = new RecordingOutput();
        CountDownLatch closed = new CountDownLatch(1);
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofSeconds(30), new AtomicInteger(), closed);
        s.start();

        out.failOnSend = true;
        stream.add("delta");
        s.wake();

        assertThat(closed.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(out.completed.await(1, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void closeWakesTheThreadWithoutWaitingForHeartbeat() throws Exception {
        MemoryStream stream = new MemoryStream();
        RecordingOutput out = new RecordingOutput();
        CountDownLatch closed = new CountDownLatch(1);
        StreamSubscriber s = subscriber(stream, out, RelayKeys.FROM_START, Duration.ofMinutes(1), new AtomicInteger(), closed);
        s.start();

        s.close();

        assertThat(closed.await(2, TimeUnit.SECONDS)).isTrue();
    }

    @Test
    void malformedLastEventIdStartsFromBeginning() {
        assertThat(RelayKeys.startAfter(null)).isEqualTo(RelayKeys.FROM_START);
        assertThat(RelayKeys.startAfter("")).isEqualTo(RelayKeys.FROM_START);
        assertThat(RelayKeys.startAfter("abc")).isEqualTo(RelayKeys.FROM_START);
        assertThat(RelayKeys.startAfter("1700000000000")).isEqualTo(RelayKeys.FROM_START);
        assertThat(RelayKeys.startAfter(" 1700000000000-3 ")).isEqualTo("1700000000000-3");
    }
}
