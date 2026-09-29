package com.odh.chat.web;

import com.odh.chat.answer.AnswerStatus;
import com.odh.chat.conversation.ConversationDtos;
import com.odh.chat.conversation.ConversationStore;
import com.odh.chat.job.AnswerJob;
import com.odh.chat.job.AnswerJobProducer;
import com.odh.chat.job.AnswerJobStore;
import com.odh.chat.job.JobStatus;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.common.InternalAuth;
import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.ReadOffset;
import org.springframework.data.redis.connection.stream.StreamOffset;
import org.springframework.data.redis.connection.stream.StreamReadOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * chat-service 의 web role. HTTP API 와 SSE 커넥션을 담당한다.
 *
 * 답변 생성 자체는 하지 않는다 — job 을 만들어 Kafka 로 넘기고,
 * 그 job 이 Redis Stream 에 남기는 이벤트를 tail 해서 브라우저로 relay 할 뿐이다.
 * 실제 처리는 rag-service(Python/LangGraph)가 독립적인 프로세스에서 수행한다.
 */
@RestController
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);
    /** SSE 연결 상한. 게이트웨이 응답 타임아웃 300초와 맞춘다. */
    private static final long SSE_TIMEOUT_MS = 300_000;
    private static final long HEARTBEAT_SECONDS = 15;
    private static final Duration STREAM_BLOCK = Duration.ofSeconds(5);

    private final ConversationStore store;
    private final AskService askService;
    private final AnswerJobStore jobs;
    private final AnswerJobProducer jobProducer;
    private final StringRedisTemplate redis;
    // 상한 없는 풀(cachedThreadPool)이면 워커가 죽어 스트림에 done/error가 안 남고
    // 클라이언트도 끊기지 않을 때(또는 끊긴 걸 못 알아챌 때) 스레드가 계속 쌓인다.
    // SynchronousQueue + 상한으로 무한 증식을 막는다. 실제 누수는 tailStream의 종료 조건으로 막는다
    private final ExecutorService workers = new ThreadPoolExecutor(
            4, 200, 60L, TimeUnit.SECONDS, new SynchronousQueue<>());
    private final ScheduledExecutorService heartbeats = Executors.newScheduledThreadPool(2);

    public ChatController(ConversationStore store, AskService askService, AnswerJobStore jobs,
                          AnswerJobProducer jobProducer, StringRedisTemplate redis) {
        this.store = store;
        this.askService = askService;
        this.jobs = jobs;
        this.jobProducer = jobProducer;
        this.redis = redis;
    }

    @jakarta.annotation.PreDestroy
    void shutdown() {
        heartbeats.shutdownNow();
        workers.shutdownNow();
    }

    @GetMapping("/api/conversations")
    public PageResponse<ConversationDtos.Conversation> list(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        PageRequest paging = PageRequest.of(page, size);
        long total = store.countConversations(userId);
        return PageResponse.of(store.list(userId, paging), paging.page(), paging.size(), total);
    }

    @PostMapping("/api/conversations")
    public ResponseEntity<ConversationDtos.Conversation> create(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId) {
        long id = store.createConversation(userId, "새 대화");
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(store.findOwned(id, userId).orElseThrow());
    }

    @GetMapping("/api/conversations/{conversationId}")
    public ResponseEntity<?> get(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                 @PathVariable Long conversationId) {
        return store.findOwned(conversationId, userId)
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(this::notFound);
    }

    @DeleteMapping("/api/conversations/{conversationId}")
    public ResponseEntity<?> delete(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                    @PathVariable Long conversationId) {
        return store.softDelete(conversationId, userId)
                ? ResponseEntity.noContent().build()
                : notFound();
    }

    @GetMapping("/api/conversations/{conversationId}/messages")
    public ResponseEntity<?> messages(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                      @PathVariable Long conversationId) {
        if (store.findOwned(conversationId, userId).isEmpty()) {
            return notFound();
        }
        return ResponseEntity.ok(store.messages(conversationId));
    }

    /**
     * 응답은 text/event-stream 이다. 이벤트 순서는 conversation → sources → delta → done.
     * 이 메서드는 job 을 만들어 Kafka 로 넘기기만 하고, 실제 생성은 worker 가 한다.
     */
    @PostMapping(value = "/api/conversations/{conversationId}/messages",
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter ask(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                          @PathVariable Long conversationId,
                          @Valid @RequestBody ConversationDtos.AskRequest request) {

        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT_MS);
        SseSink sink = new SseSink(emitter);

        if (store.findOwned(conversationId, userId).isEmpty()) {
            sink.error(ErrorCode.NOT_FOUND, "대화를 찾을 수 없습니다.");
            emitter.complete();
            return emitter;
        }

        UUID jobKey = jobs.createPending(conversationId, userId, request.content());
        jobProducer.publish(jobKey);
        sink.conversation(conversationId, jobKey);

        tailStream(emitter, sink, jobKey, ReadOffset.from("0"));
        return emitter;
    }

    /**
     * 재연결 시 이어받기. Last-Event-ID 이후 구간부터 이어서 tail 한다.
     * job 이 이미 끝났으면(COMPLETED/FAILED) done/error 한 번만 보낸다 —
     * 프론트가 어차피 스트림 종료 후 메시지 목록을 재조회하므로 델타를 재구성할 필요가 없다.
     */
    @GetMapping(value = "/api/conversations/{conversationId}/jobs/{jobKey}/stream",
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter resume(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                             @PathVariable Long conversationId,
                             @PathVariable UUID jobKey,
                             @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId) {

        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT_MS);
        SseSink sink = new SseSink(emitter);

        if (store.findOwned(conversationId, userId).isEmpty()) {
            sink.error(ErrorCode.NOT_FOUND, "대화를 찾을 수 없습니다.");
            emitter.complete();
            return emitter;
        }

        Optional<AnswerJob> job = jobs.findByKey(jobKey).filter(j -> j.conversationId() == conversationId);
        if (job.isEmpty()) {
            sink.error(ErrorCode.NOT_FOUND, "작업을 찾을 수 없습니다.");
            emitter.complete();
            return emitter;
        }

        JobStatus status = job.get().status();
        if (status == JobStatus.COMPLETED || status == JobStatus.FAILED) {
            finishAlreadyDone(sink, job.get());
            emitter.complete();
            return emitter;
        }

        String from = (lastEventId == null || lastEventId.isBlank()) ? "0" : lastEventId;
        tailStream(emitter, sink, jobKey, ReadOffset.from(from));
        return emitter;
    }

    private void finishAlreadyDone(SseSink sink, AnswerJob job) {
        if (job.status() == JobStatus.FAILED || job.assistantMessageId() == null) {
            sink.error("ANSWER_FAILED", "답변을 만들지 못했습니다.");
            return;
        }
        ConversationDtos.Message message = store.findMessage(job.assistantMessageId()).orElse(null);
        if (message == null) {
            sink.error("ANSWER_FAILED", "답변을 만들지 못했습니다.");
            return;
        }
        sink.done(message.id(), AnswerStatus.valueOf(message.answerStatus()), message.modelName(),
                message.latencyMs() == null ? 0 : message.latencyMs(), List.of());
    }

    /** Redis Stream "chat:answer:stream:{jobKey}" 를 tail 하며 SSE 로 relay 한다. */
    private void tailStream(SseEmitter emitter, SseSink sink, UUID jobKey, ReadOffset from) {
        ScheduledFuture<?> beat = heartbeats.scheduleAtFixedRate(
                sink::heartbeat, HEARTBEAT_SECONDS, HEARTBEAT_SECONDS, TimeUnit.SECONDS);

        // 클라이언트가 끊기거나(하트비트 실패 → sink.isClosed()) emitter 가 타임아웃/완료돼도
        // 스트림에 새 레코드가 안 들어오면 while(true)이 그 사실을 알 길이 없어 스레드가
        // 계속 blocking read를 반복했다. 콜백으로 즉시 알아채게 한다
        AtomicBoolean active = new AtomicBoolean(true);
        emitter.onCompletion(() -> active.set(false));
        emitter.onTimeout(() -> { active.set(false); emitter.complete(); });
        emitter.onError(e -> active.set(false));

        workers.submit(() -> {
            String streamKey = "chat:answer:stream:" + jobKey;
            ReadOffset offset = from;
            try {
                while (active.get() && !sink.isClosed()) {
                    List<MapRecord<String, Object, Object>> records = redis.opsForStream().read(
                            StreamReadOptions.empty().count(50).block(STREAM_BLOCK),
                            StreamOffset.create(streamKey, offset));
                    if (records == null || records.isEmpty()) {
                        continue;
                    }
                    for (MapRecord<String, Object, Object> record : records) {
                        offset = ReadOffset.from(record.getId().getValue());
                        if (relay(emitter, record)) {
                            return;
                        }
                    }
                }
            } catch (IOException e) {
                // 사용자가 화면을 닫으면 여기로 온다. 오류가 아니라 정상 종료다.
                log.debug("SSE 연결이 끊겼다 jobKey={}: {}", jobKey, e.getMessage());
            } catch (Exception e) {
                log.error("답변 스트림 릴레이 실패 jobKey={}", jobKey, e);
            } finally {
                beat.cancel(false);
                emitter.complete();
            }
        });
    }

    /** @return true 면 done/error 로 스트림이 끝났다는 뜻 */
    private boolean relay(SseEmitter emitter, MapRecord<String, Object, Object> record) throws IOException {
        Map<Object, Object> value = record.getValue();
        String event = String.valueOf(value.get("event"));
        String data = String.valueOf(value.get("data"));
        emitter.send(SseEmitter.event().id(record.getId().getValue()).name(event).data(data));
        return "done".equals(event) || "error".equals(event);
    }

    @GetMapping("/api/messages/{messageId}/sources")
    public ResponseEntity<?> sources(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                     @PathVariable Long messageId) {
        if (store.ownerOfMessage(messageId).filter(userId::equals).isEmpty()) {
            return notFound();
        }
        return ResponseEntity.ok(askService.sourcesOf(userId, messageId));
    }

    @PutMapping("/api/messages/{messageId}/feedback")
    public ResponseEntity<?> feedback(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                      @PathVariable Long messageId,
                                      @Valid @RequestBody ConversationDtos.FeedbackRequest request) {
        if (store.ownerOfMessage(messageId).filter(userId::equals).isEmpty()) {
            return notFound();
        }
        // 오류 사유는 유용하지 않음일 때만 남긴다
        String reason = "NOT_HELPFUL".equals(request.rating()) ? request.reason() : null;
        store.upsertFeedback(messageId, userId, request.rating(), reason);
        return ResponseEntity.ok(java.util.Map.of("messageId", messageId, "rating", request.rating()));
    }

    private ResponseEntity<?> notFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ErrorResponse.of(ErrorCode.NOT_FOUND, "대상을 찾을 수 없습니다.", null));
    }
}
