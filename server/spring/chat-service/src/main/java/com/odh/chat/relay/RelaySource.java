package com.odh.chat.relay;

import java.util.List;

/** 답변 Stream 읽기. 테스트에서 Redis 없이 바꿔 끼우려고 뗐다. */
public interface RelaySource {

    /** {@code afterId} 보다 뒤에 있는 레코드를 오래된 순으로 최대 {@code count} 개. 블로킹하지 않는다. */
    List<RelayRecord> after(String streamKey, String afterId, int count);
}
