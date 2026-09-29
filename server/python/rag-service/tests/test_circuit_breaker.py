from app.generation.circuit_breaker import CircuitBreaker, CircuitState


def test_circuit_breaker_state_transitions():
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout_seconds=0.1)

    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_call() is True

    # 1~2회 실패 -> 여전히 CLOSED
    breaker.record_failure("error 1")
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure("error 2")
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_call() is True

    # 3회 실패 -> 임계치 도달로 OPEN 전환
    breaker.record_failure("error 3")
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_call() is False

    # 타임아웃 경과 후 HALF_OPEN 전환
    import time
    time.sleep(0.15)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.can_call() is True

    # HALF_OPEN 상태에서 성공 -> CLOSED로 정상 복구
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_call() is True


def test_circuit_breaker_half_open_failure_reopens():
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout_seconds=0.05)

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    import time
    time.sleep(0.06)
    assert breaker.state == CircuitState.HALF_OPEN

    # HALF_OPEN에서 단 1회 실패해도 즉시 다시 OPEN
    breaker.record_failure("retry failed")
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_call() is False
