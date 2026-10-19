package com.odh.chat.relay;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * SSE 연결 하나의 릴레이. 알림을 받으면 Stream 에서 "마지막으로 보낸 레코드 다음"부터 읽어 보낸다.
 *
 * 데이터는 언제나 Stream 에서 순서대로 읽으므로 알림이 몇 개 사라져도 다음 알림이나 하트비트 주기의
 * 확인에서 빠짐없이 읽힌다. 따라잡기·실시간·재연결이 모두 같은 {@link #drain()} 하나다.
 *
 * {@link RelayOutput} 을 부르는 건 이 객체의 가상 스레드 하나뿐이다(하트비트 포함). 느린 클라이언트는
 * 자기 스레드만 늦출 뿐이고, 레코드는 Stream 에 남아 있으므로 따로 쌓아 둘 큐가 없다.
 */
public final class StreamSubscriber {

    private static final Logger log = LoggerFactory.getLogger(StreamSubscriber.class);

    private final UUID jobKey;
    private final String streamKey;
    private final RelaySource source;
    private final RelayOutput output;
    private final Duration heartbeat;
    private final int batchSize;
    private final Runnable onTickCatch;
    private final Runnable onClose;

    // wake 를 합친다: 알림이 여러 번 와도 대기 중인 신호는 하나다
    private final AtomicBoolean pending = new AtomicBoolean();
    private final Semaphore signal = new Semaphore(0);
    private volatile boolean closed;
    private String lastId;

    /**
     * @param onTickCatch 하트비트 주기 확인에서 레코드가 나왔을 때 — 알림이 사라졌다는 신호
     * @param onClose     스레드가 끝날 때 한 번. hub 에서 해제하는 데 쓴다
     */
    StreamSubscriber(UUID jobKey, String startAfter, RelaySource source, RelayOutput output,
                     Duration heartbeat, int batchSize, Runnable onTickCatch, Runnable onClose) {
        this.jobKey = jobKey;
        this.streamKey = RelayKeys.stream(jobKey);
        this.lastId = startAfter;
        this.source = source;
        this.output = output;
        this.heartbeat = heartbeat;
        this.batchSize = batchSize;
        this.onTickCatch = onTickCatch;
        this.onClose = onClose;
    }

    public UUID jobKey() {
        return jobKey;
    }

    public void start() {
        Thread.ofVirtual().name("sse-relay-" + jobKey).start(this::run);
    }

    /** 알림 수신 스레드에서 불린다. 막히지 않는다. */
    public void wake() {
        if (pending.compareAndSet(false, true)) {
            signal.release();
        }
    }

    /** 클라이언트 끊김·타임아웃·종료. 스레드를 깨워 정리하게 한다. */
    public void close() {
        closed = true;
        signal.release();
    }

    private void run() {
        try {
            boolean woken = true;   // 등록 직후 첫 읽기 = 이미 쌓인 레코드 따라잡기
            while (!closed) {
                if (!woken) {
                    output.heartbeat();
                }
                pending.set(false);
                int read = drain();
                if (!woken && read > 0) {
                    onTickCatch.run();
                }
                if (closed) {
                    break;
                }
                woken = signal.tryAcquire(heartbeat.toMillis(), TimeUnit.MILLISECONDS);
            }
        } catch (IOException e) {
            // 사용자가 화면을 닫으면 여기로 온다. 오류가 아니라 정상 종료다.
            log.debug("SSE 연결이 끊겼다 jobKey={}: {}", jobKey, e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (RuntimeException e) {
            // Redis 가 내려간 경우 등. 오류 이벤트를 보내지 않고 닫는다 — 브라우저가 Last-Event-ID 로 다시 붙는다
            log.warn("답변 스트림 릴레이 중단 jobKey={}: {}", jobKey, e.toString());
        } finally {
            closed = true;
            onClose.run();
            output.complete();
        }
    }

    /** @return 이번에 보낸 레코드 수. done/error 를 보내면 닫힌 상태가 된다 */
    private int drain() throws IOException {
        int sent = 0;
        while (!closed) {
            List<RelayRecord> records = source.after(streamKey, lastId, batchSize);
            for (RelayRecord record : records) {
                output.event(record);
                lastId = record.id();
                sent++;
                if (record.terminal()) {
                    closed = true;
                    return sent;
                }
            }
            if (records.size() < batchSize) {
                break;
            }
        }
        return sent;
    }
}
