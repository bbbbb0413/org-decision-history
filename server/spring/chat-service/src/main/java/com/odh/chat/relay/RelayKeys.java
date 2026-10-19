package com.odh.chat.relay;

import java.util.UUID;
import java.util.regex.Pattern;

/**
 * rag-service 와 맞춘 Redis 키 이름. 바꾸면 rag-service {@code app/redis_sink.py} 도 함께 바꿔야 한다.
 */
public final class RelayKeys {

    /** Stream ID 형식(밀리초-순번). 이 형식이 아니면 처음부터 읽는다. */
    private static final Pattern STREAM_ID = Pattern.compile("\\d+-\\d+");

    private static final String CHANNEL_PREFIX = "chat:answer:live:";

    /** 처음부터 읽을 때의 시작점. 0-0 보다 큰 레코드는 모두 읽힌다. */
    public static final String FROM_START = "0-0";

    private RelayKeys() {
    }

    /** 답변 이벤트를 보관하는 Stream. 데이터의 원본이다. */
    public static String stream(UUID jobKey) {
        return "chat:answer:stream:" + jobKey;
    }

    /** "새 레코드 있음" 알림 채널. 데이터는 싣지 않는다 — 받으면 Stream 을 읽는다. */
    public static String channel(UUID jobKey) {
        return CHANNEL_PREFIX + jobKey;
    }

    /**
     * 브라우저가 보낸 Last-Event-ID 를 읽기 시작점으로 바꾼다. 비었거나 형식이 틀리면 처음부터 —
     * 이어받기를 오류로 끊는 것보다 전체 재전송이 사용자에게 낫다.
     */
    public static String startAfter(String lastEventId) {
        if (lastEventId == null || !STREAM_ID.matcher(lastEventId.trim()).matches()) {
            return FROM_START;
        }
        return lastEventId.trim();
    }
}
