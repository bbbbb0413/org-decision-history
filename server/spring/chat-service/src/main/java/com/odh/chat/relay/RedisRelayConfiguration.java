package com.odh.chat.relay;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.Limit;
import org.springframework.data.redis.connection.MessageListener;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.listener.ChannelTopic;
import org.springframework.data.redis.listener.RedisMessageListenerContainer;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** {@link RelaySource}·{@link RelayChannels} 의 Redis 구현. */
@Configuration
public class RedisRelayConfiguration {

    /**
     * XRANGE 는 블로킹 명령이 아니라 공유 커넥션으로 간다 — 새 TCP 연결을 만들지 않는다.
     * 블로킹 XREAD 는 전용 연결이 필요해 읽기마다 연결을 새로 열고, 닫힌 소켓이 TIME_WAIT 로 남아 임시 포트를
     * 고갈시킨다. 이를 피하는 핵심이다.
     *
     * 시작점을 포함하는 범위로 읽고 시작점 레코드는 버린다. 배타 범위("(id")는 드라이버 변환에 기대지 않으려고 쓰지 않는다.
     */
    @Bean
    RelaySource relaySource(StringRedisTemplate redis) {
        return (streamKey, afterId, count) -> {
            List<MapRecord<String, Object, Object>> records = redis.opsForStream().range(streamKey,
                    Range.rightUnbounded(Range.Bound.inclusive(afterId)), Limit.limit().count(count + 1));
            List<RelayRecord> result = new ArrayList<>(records == null ? 0 : records.size());
            if (records == null) {
                return result;
            }
            for (MapRecord<String, Object, Object> record : records) {
                String id = record.getId().getValue();
                if (id.equals(afterId)) {
                    continue;
                }
                Map<Object, Object> value = record.getValue();
                result.add(new RelayRecord(id, String.valueOf(value.get("event")), String.valueOf(value.get("data"))));
                if (result.size() == count) {
                    break;
                }
            }
            return result;
        };
    }

    /**
     * 구독 커넥션 하나. 리스너를 수신 스레드에서 바로 부른다(기본 실행기는 메시지마다 스레드를 만든다) —
     * 리스너는 구독자를 깨우기만 해서 막히지 않는다.
     * 처음 구독할 때 연결한다. POLL 모드에서는 연결하지 않는다.
     */
    @Bean(destroyMethod = "destroy")
    RedisMessageListenerContainer relayListenerContainer(RedisConnectionFactory connectionFactory) {
        RedisMessageListenerContainer container = new RedisMessageListenerContainer();
        container.setConnectionFactory(connectionFactory);
        container.setTaskExecutor(Runnable::run);
        return container;
    }

    @Bean
    RelayChannels relayChannels(RedisMessageListenerContainer container) {
        return new ContainerRelayChannels(container);
    }

    /**
     * job 별 채널을 컨테이너로 구독한다. 컨테이너를 항상 "구독 중" 상태로 둬야 한다:
     *
     * Spring Data Redis 3.5 {@code RedisMessageListenerContainer.addListener} 는 컨테이너가 구독 연결을
     * 준비하는 중(prepareListening)에 들어온 채널을 따로 구독하지 않는다 — 준비를 시작할 때 떠 둔 채널
     * 목록만 구독되므로 그 뒤에 추가된 채널은 빠진다. 또 리스너가 하나도 남지 않으면 연결을 닫아
     * (stopListening) 다음 부하 때 같은 경쟁이 되풀이된다. 앵커가 없으면 동시 200 job 중 22개가
     * 이렇게 구독되지 않았다.
     *
     * 그래서 첫 구독 전에 영구 앵커 채널 하나를 구독하고 구독 중 상태가 될 때까지 기다린다. 이후의 추가는
     * 모두 "이미 구독 중" 경로를 타서 각 채널의 구독 확정까지 기다린 뒤 돌아온다.
     */
    static final class ContainerRelayChannels implements RelayChannels {

        static final String ANCHOR_CHANNEL = "chat:answer:live:anchor";
        private static final long LISTEN_WAIT_MILLIS = 10_000;

        private final RedisMessageListenerContainer container;
        private final Map<String, MessageListener> listeners = new ConcurrentHashMap<>();
        private volatile boolean anchored;

        ContainerRelayChannels(RedisMessageListenerContainer container) {
            this.container = container;
        }

        @Override
        public void subscribe(String channel, Runnable onMessage) {
            ensureListening();
            MessageListener listener = (message, pattern) -> onMessage.run();
            listeners.put(channel, listener);
            container.addMessageListener(listener, ChannelTopic.of(channel));
        }

        @Override
        public void unsubscribe(String channel) {
            MessageListener listener = listeners.remove(channel);
            if (listener != null) {
                container.removeMessageListener(listener, ChannelTopic.of(channel));
            }
        }

        private void ensureListening() {
            if (anchored) {
                return;
            }
            synchronized (this) {
                if (anchored) {
                    return;
                }
                container.addMessageListener((message, pattern) -> { }, ChannelTopic.of(ANCHOR_CHANNEL));
                long deadline = System.currentTimeMillis() + LISTEN_WAIT_MILLIS;
                while (!container.isListening()) {
                    if (System.currentTimeMillis() > deadline) {
                        throw new IllegalStateException("Redis 알림 구독 연결을 " + LISTEN_WAIT_MILLIS + "ms 안에 열지 못했다");
                    }
                    try {
                        Thread.sleep(10);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new IllegalStateException("Redis 알림 구독 연결 대기 중 인터럽트", e);
                    }
                }
                anchored = true;
            }
        }
    }
}
