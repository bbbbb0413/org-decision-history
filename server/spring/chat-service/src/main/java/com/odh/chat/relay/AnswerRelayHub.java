package com.odh.chat.relay;

import com.odh.chat.web.SseProperties;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 인스턴스당 하나. 답변 job 별 알림 채널을 참조 카운트로 구독하고, 알림이 오면 그 job 의 구독자를 깨운다.
 *
 * 알림은 그 job 을 보고 있는 인스턴스에만 간다 — 모든 job 을 패턴 하나로 받으면 인스턴스 수만큼 알림이
 * 복제돼 대부분 버려진다. 같은 job 을 탭 두 개가 볼 수 있어 job 당 구독자는 여럿이다. 첫 구독자에서
 * 채널을 구독하고 마지막 구독자가 빠지면 해제한다.
 */
@Component
public class AnswerRelayHub implements DisposableBean {

    private final RelayChannels channels;
    private final RelaySource source;
    private final SseProperties sse;
    private final Counter tickCatches;
    private final Counter unrouted;
    private final AtomicInteger active = new AtomicInteger();
    private final Map<UUID, JobEntry> jobs = new ConcurrentHashMap<>();

    public AnswerRelayHub(RelayChannels channels, RelaySource source, SseProperties sse, MeterRegistry meters) {
        this.channels = channels;
        this.source = source;
        this.sse = sse;
        this.tickCatches = Counter.builder("relay.wake.missed")
                .description("하트비트 주기 확인에서야 읽힌 횟수 — 알림이 사라졌다는 신호. 정상 부하에서 0 이어야 한다")
                .register(meters);
        this.unrouted = Counter.builder("relay.notifications.unrouted")
                .description("받았지만 이 인스턴스에 구독자가 없어 버린 알림 — 팬아웃 낭비. 구독 해제 직후 몇 건 외에는 0 이어야 한다")
                .register(meters);
        Gauge.builder("relay.subscribers.active", active, AtomicInteger::get)
                .description("열려 있는 답변 SSE 릴레이 수")
                .register(meters);
    }

    /**
     * 구독자를 만들고 채널 구독이 확정될 때까지 기다린다. 시작은 호출 쪽이 SSE 콜백을 건 뒤
     * {@link StreamSubscriber#start()} 로 한다.
     *
     * @param startAfter 이 ID 다음 레코드부터 보낸다 ({@link RelayKeys#startAfter})
     */
    public StreamSubscriber open(UUID jobKey, String startAfter, RelayOutput output) {
        StreamSubscriber[] self = new StreamSubscriber[1];
        StreamSubscriber subscriber = new StreamSubscriber(jobKey, startAfter, source, output,
                sse.heartbeat(), sse.drainBatchSize(), tickCatches::increment,
                () -> unregister(jobKey, self[0]));
        self[0] = subscriber;
        register(jobKey, subscriber);
        return subscriber;
    }

    private void register(UUID jobKey, StreamSubscriber subscriber) {
        while (true) {
            JobEntry entry = jobs.computeIfAbsent(jobKey, k -> new JobEntry());
            synchronized (entry) {
                if (entry.removed) {
                    continue;   // 마지막 구독자가 막 빠지며 해제된 항목이다. 새로 만든다
                }
                if (!entry.subscribed) {
                    channels.subscribe(RelayKeys.channel(jobKey), () -> wakeAll(jobKey));
                    entry.subscribed = true;
                }
                entry.subscribers.add(subscriber);
            }
            active.incrementAndGet();
            return;
        }
    }

    /**
     * 해제(UNSUBSCRIBE)를 끝낸 뒤에 맵에서 지운다. 거꾸로 하면 같은 job 을 새로 여는 쪽의 SUBSCRIBE 가
     * 이 UNSUBSCRIBE 보다 먼저 나가 새 구독자가 알림을 못 받을 수 있다 — 새로 여는 쪽은 이 항목의 락을
     * 기다렸다가 removed 를 보고 새 항목을 만든다.
     */
    private void unregister(UUID jobKey, StreamSubscriber subscriber) {
        JobEntry entry = jobs.get(jobKey);
        if (entry == null) {
            return;
        }
        synchronized (entry) {
            if (!entry.subscribers.remove(subscriber)) {
                return;
            }
            active.decrementAndGet();
            if (entry.subscribers.isEmpty()) {
                entry.removed = true;
                try {
                    channels.unsubscribe(RelayKeys.channel(jobKey));
                } finally {
                    jobs.remove(jobKey, entry);
                }
            }
        }
    }

    /** 알림 수신 스레드에서 불린다. 락을 잡지 않는다 — 구독 확정을 기다리는 쪽과 서로 기다리는 일이 없게. */
    private void wakeAll(UUID jobKey) {
        JobEntry entry = jobs.get(jobKey);
        if (entry == null || entry.subscribers.isEmpty()) {
            unrouted.increment();
            return;
        }
        entry.subscribers.forEach(StreamSubscriber::wake);
    }

    int activeCount() {
        return active.get();
    }

    @Override
    public void destroy() {
        jobs.values().forEach(entry -> entry.subscribers.forEach(StreamSubscriber::close));
    }

    private static final class JobEntry {
        final Set<StreamSubscriber> subscribers = ConcurrentHashMap.newKeySet();
        boolean subscribed;
        boolean removed;
    }
}
