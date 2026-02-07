"""Unit tests for RetryMixin covering retry exhaustion, temperature adjustment,
exit conditions, and the circuit breaker.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from openhands.core.exceptions import LLMNoResponseError
from openhands.llm.retry_mixin import (
    CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    LLMCircuitBreakerError,
    RetryMixin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeLLM(RetryMixin):
    """Minimal host class that uses RetryMixin without a real LLM backend."""

    pass


def _make_retry_decorator(
    mixin: RetryMixin,
    *,
    num_retries: int = 3,
    retry_exceptions: tuple = (RuntimeError,),
    retry_min_wait: float = 0,
    retry_max_wait: float = 0.01,
    retry_multiplier: float = 0.001,
) -> callable:
    """Create a retry decorator with fast timings suitable for tests."""
    return mixin.retry_decorator(
        num_retries=num_retries,
        retry_exceptions=retry_exceptions,
        retry_min_wait=retry_min_wait,
        retry_max_wait=retry_max_wait,
        retry_multiplier=retry_multiplier,
    )


# ---------------------------------------------------------------------------
# 1. Retry exhaustion
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    """Verify that max retries are respected and the final exception propagates."""

    def test_sync_max_retries_respected(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(mixin, num_retries=3)
        call_count = 0

        @decorator
        def failing():
            nonlocal call_count
            call_count += 1
            raise RuntimeError('boom')

        with pytest.raises(RuntimeError, match='boom'):
            failing()

        # tenacity stop_after_attempt(3) means attempts 1, 2, 3
        assert call_count == 3

    def test_sync_succeeds_before_exhaustion(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(mixin, num_retries=5)
        call_count = 0

        @decorator
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError('transient')
            return 'ok'

        assert flaky() == 'ok'
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_async_max_retries_respected(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(mixin, num_retries=2)
        call_count = 0

        @decorator
        async def failing():
            nonlocal call_count
            call_count += 1
            raise RuntimeError('async boom')

        with pytest.raises(RuntimeError, match='async boom'):
            await failing()

        assert call_count == 2


# ---------------------------------------------------------------------------
# 2. Temperature adjustment on LLMNoResponseError with temp=0
# ---------------------------------------------------------------------------


class TestTemperatureAdjustment:
    """Verify temperature gets bumped to 1.0 when LLMNoResponseError fires at temp 0."""

    def test_temperature_set_to_one_on_no_response(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(
            mixin,
            num_retries=3,
            retry_exceptions=(LLMNoResponseError,),
        )
        captured_temps = []

        @decorator
        def llm_call(**kwargs):
            captured_temps.append(kwargs.get('temperature', 'unset'))
            if len(captured_temps) < 3:
                raise LLMNoResponseError()
            return 'ok'

        result = llm_call(temperature=0)
        assert result == 'ok'
        # First call: temperature=0, second call: temperature should be 1.0
        # (set by before_sleep), third call: temperature should still be 1.0
        assert captured_temps[0] == 0
        assert captured_temps[1] == 1.0

    def test_nonzero_temperature_not_changed(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(
            mixin,
            num_retries=3,
            retry_exceptions=(LLMNoResponseError,),
        )
        captured_temps = []

        @decorator
        def llm_call(**kwargs):
            captured_temps.append(kwargs.get('temperature', 'unset'))
            if len(captured_temps) < 2:
                raise LLMNoResponseError()
            return 'ok'

        result = llm_call(temperature=0.7)
        assert result == 'ok'
        # Temperature should remain 0.7 because it was nonzero
        assert captured_temps[0] == 0.7
        assert captured_temps[1] == 0.7


# ---------------------------------------------------------------------------
# 3. Non-retry exceptions
# ---------------------------------------------------------------------------


class TestNonRetryExceptions:
    """Verify that exceptions not in retry_exceptions are not retried."""

    def test_non_retryable_exception_propagates_immediately(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(
            mixin, num_retries=5, retry_exceptions=(RuntimeError,)
        )
        call_count = 0

        @decorator
        def failing():
            nonlocal call_count
            call_count += 1
            raise ValueError('not retryable')

        with pytest.raises(ValueError, match='not retryable'):
            failing()

        assert call_count == 1


# ---------------------------------------------------------------------------
# 4. Exit condition (should_exit)
# ---------------------------------------------------------------------------


class TestExitCondition:
    """Verify that stop_if_should_exit() stops retries when shutdown is signalled."""

    @patch('openhands.utils.tenacity_stop.should_exit', return_value=True)
    def test_retries_stop_when_should_exit(self, mock_exit):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(mixin, num_retries=10)
        call_count = 0

        @decorator
        def failing():
            nonlocal call_count
            call_count += 1
            raise RuntimeError('keep going')

        with pytest.raises(RuntimeError):
            failing()

        # should_exit is True so retries stop after first failure
        assert call_count <= 2


# ---------------------------------------------------------------------------
# 5. Circuit breaker opens after threshold failures
# ---------------------------------------------------------------------------


class TestCircuitBreakerOpens:
    """Verify the circuit breaker opens after CIRCUIT_BREAKER_FAILURE_THRESHOLD consecutive failures."""

    def test_opens_after_threshold_failures(self):
        mixin = _FakeLLM()

        # Simulate consecutive failures
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            mixin._record_circuit_breaker_failure()

        assert mixin._cb_failures == CIRCUIT_BREAKER_FAILURE_THRESHOLD
        assert mixin._cb_open_time > 0.0

        with pytest.raises(LLMCircuitBreakerError) as exc_info:
            mixin._check_circuit_breaker()

        assert exc_info.value.cooldown_remaining > 0

    def test_does_not_open_below_threshold(self):
        mixin = _FakeLLM()

        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD - 1):
            mixin._record_circuit_breaker_failure()

        # Should not raise
        mixin._check_circuit_breaker()


# ---------------------------------------------------------------------------
# 6. Circuit breaker half-open after cooldown
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalfOpen:
    """After the cooldown period, the circuit transitions to half-open and allows one request."""

    def test_allows_request_after_cooldown(self):
        mixin = _FakeLLM()

        # Open the circuit breaker
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            mixin._record_circuit_breaker_failure()

        # Fast-forward past cooldown by manipulating the open time
        mixin._cb_open_time = time.monotonic() - CIRCUIT_BREAKER_COOLDOWN_SECONDS - 1

        # Should NOT raise (half-open state)
        mixin._check_circuit_breaker()

    def test_still_rejects_during_cooldown(self):
        mixin = _FakeLLM()

        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            mixin._record_circuit_breaker_failure()

        # Open time is recent (just set by _record_circuit_breaker_failure)
        with pytest.raises(LLMCircuitBreakerError):
            mixin._check_circuit_breaker()


# ---------------------------------------------------------------------------
# 7. Circuit breaker resets on success
# ---------------------------------------------------------------------------


class TestCircuitBreakerResets:
    """Verify that a successful call resets the failure counter and open time."""

    def test_resets_on_success(self):
        mixin = _FakeLLM()

        # Accumulate some failures (but below threshold)
        for _ in range(3):
            mixin._record_circuit_breaker_failure()

        assert mixin._cb_failures == 3

        mixin._record_circuit_breaker_success()

        assert mixin._cb_failures == 0
        assert mixin._cb_open_time == 0.0

    def test_resets_from_half_open_on_success(self):
        mixin = _FakeLLM()

        # Open the circuit breaker
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            mixin._record_circuit_breaker_failure()

        # Fast-forward past cooldown
        mixin._cb_open_time = time.monotonic() - CIRCUIT_BREAKER_COOLDOWN_SECONDS - 1

        # Half-open: allow request
        mixin._check_circuit_breaker()

        # Record success
        mixin._record_circuit_breaker_success()

        assert mixin._cb_failures == 0
        assert mixin._cb_open_time == 0.0

        # Should freely allow requests now
        mixin._check_circuit_breaker()


# ---------------------------------------------------------------------------
# 8. Circuit breaker reopens on failure in half-open
# ---------------------------------------------------------------------------


class TestCircuitBreakerReopens:
    """Verify that a failure in half-open state reopens the circuit breaker."""

    def test_reopens_on_half_open_failure(self):
        mixin = _FakeLLM()

        # Open the circuit
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            mixin._record_circuit_breaker_failure()

        # Fast-forward past cooldown
        mixin._cb_open_time = time.monotonic() - CIRCUIT_BREAKER_COOLDOWN_SECONDS - 1

        # Half-open: allow request
        mixin._check_circuit_breaker()

        # Request fails again
        mixin._record_circuit_breaker_failure()

        # Circuit should be open again with a fresh cooldown
        with pytest.raises(LLMCircuitBreakerError):
            mixin._check_circuit_breaker()


# ---------------------------------------------------------------------------
# 9. End-to-end: circuit breaker integrates with retry decorator
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """Verify that the circuit breaker integrates correctly with the retry decorator."""

    def test_circuit_breaker_blocks_after_repeated_exhaustion(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(
            mixin,
            num_retries=1,
            retry_exceptions=(RuntimeError,),
        )

        @decorator
        def failing():
            raise RuntimeError('always fails')

        # Exhaust retries enough times to trip the circuit breaker
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            with pytest.raises(RuntimeError):
                failing()

        # Next call should be immediately rejected by the circuit breaker
        with pytest.raises(LLMCircuitBreakerError):
            failing()

    def test_circuit_breaker_resets_after_success_through_decorator(self):
        mixin = _FakeLLM()
        decorator = _make_retry_decorator(
            mixin,
            num_retries=2,
            retry_exceptions=(RuntimeError,),
        )
        call_count = 0

        @decorator
        def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError('first fails')
            return 'success'

        result = eventually_succeeds()
        assert result == 'success'
        # Failures should be reset
        assert mixin._cb_failures == 0


# ---------------------------------------------------------------------------
# 10. Lazy initialization of circuit breaker attributes
# ---------------------------------------------------------------------------


class TestLazyInitialization:
    """Verify that circuit breaker attributes work without explicit __init__."""

    def test_default_values_on_fresh_instance(self):
        mixin = _FakeLLM()
        assert mixin._cb_failures == 0
        assert mixin._cb_open_time == 0.0

    def test_setter_creates_attributes(self):
        mixin = _FakeLLM()
        mixin._cb_failures = 5
        mixin._cb_open_time = 100.0
        assert mixin._cb_failures == 5
        assert mixin._cb_open_time == 100.0


# ---------------------------------------------------------------------------
# 11. LLMCircuitBreakerError properties
# ---------------------------------------------------------------------------


class TestLLMCircuitBreakerError:
    """Verify the custom error carries the expected information."""

    def test_error_message_and_cooldown(self):
        err = LLMCircuitBreakerError(cooldown_remaining=15.5)
        assert '15.5s' in str(err)
        assert str(CIRCUIT_BREAKER_FAILURE_THRESHOLD) in str(err)
        assert err.cooldown_remaining == 15.5

    def test_is_runtime_error(self):
        err = LLMCircuitBreakerError(cooldown_remaining=1.0)
        assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# 12. Retry listener callback
# ---------------------------------------------------------------------------


class TestRetryListener:
    """Verify that the retry_listener callback is invoked on each retry."""

    def test_listener_called_on_each_retry(self):
        mixin = _FakeLLM()
        listener_calls = []

        def my_listener(attempt, max_retries):
            listener_calls.append((attempt, max_retries))

        decorator = mixin.retry_decorator(
            num_retries=3,
            retry_exceptions=(RuntimeError,),
            retry_min_wait=0,
            retry_max_wait=0.01,
            retry_multiplier=0.001,
            retry_listener=my_listener,
        )
        call_count = 0

        @decorator
        def failing():
            nonlocal call_count
            call_count += 1
            raise RuntimeError('fail')

        with pytest.raises(RuntimeError):
            failing()

        # Listener should be called for each retry (not the initial attempt)
        # With num_retries=3, there are 2 before_sleep calls (after attempt 1 and 2)
        assert len(listener_calls) == 2
        assert listener_calls[0][1] == 3  # max_retries passed through
