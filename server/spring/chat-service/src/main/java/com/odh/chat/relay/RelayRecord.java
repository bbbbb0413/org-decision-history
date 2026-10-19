package com.odh.chat.relay;

/**
 * 답변 Stream 레코드 하나. rag-service 가 XADD 한 {@code event}·{@code data} 를 그대로 담는다.
 *
 * @param id    Redis Stream 레코드 ID. SSE {@code id} 로 내보내고, 브라우저가 Last-Event-ID 로 되돌려 보낸다
 * @param event SSE 이벤트 이름 (sources·graph·delta·done·error)
 * @param data  JSON 문자열. 다시 직렬화하지 않고 그대로 내보낸다
 */
public record RelayRecord(String id, String event, String data) {

    /** done/error 가 오면 그 job 의 스트림은 끝난다. */
    public boolean terminal() {
        return "done".equals(event) || "error".equals(event);
    }
}
