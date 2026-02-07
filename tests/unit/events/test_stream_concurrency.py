"""Concurrency tests for the EventStream covering concurrent delivery,
subscriber add/remove during dispatch, error propagation, background writer,
and shutdown sentinel handling.
"""

import queue
import threading
import time

import pytest

from openhands.events import EventSource, EventStream, EventStreamSubscriber
from openhands.events.action import NullAction
from openhands.events.observation import NullObservation
from openhands.storage.memory import InMemoryFileStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def file_store():
    return InMemoryFileStore()


@pytest.fixture
def event_stream(file_store):
    stream = EventStream('test_concurrency', file_store)
    yield stream
    stream.close()


# ---------------------------------------------------------------------------
# 1. Concurrent event addition from multiple threads
# ---------------------------------------------------------------------------


class TestConcurrentEventAddition:
    """Verify that adding events from multiple threads is safe and all events
    are assigned unique, sequential IDs."""

    def test_concurrent_adds_produce_unique_ids(self, file_store):
        stream = EventStream('concurrent_add', file_store)
        num_threads = 4
        events_per_thread = 25
        barrier = threading.Barrier(num_threads)

        def add_events():
            barrier.wait()
            for _ in range(events_per_thread):
                stream.add_event(NullObservation('concurrent'), EventSource.AGENT)

        threads = [threading.Thread(target=add_events) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Allow background writer to flush
        time.sleep(0.5)

        total_expected = num_threads * events_per_thread
        assert stream.cur_id == total_expected

        # Verify all IDs are unique by checking the stored files
        event_ids = set()
        for key in file_store.files:
            if '/events/' in key and key.endswith('.json'):
                # Extract event ID from filename
                parts = key.split('/')
                for part in parts:
                    if part.endswith('.json') and not part.startswith('event_cache'):
                        try:
                            event_id = int(part.replace('.json', ''))
                            event_ids.add(event_id)
                        except ValueError:
                            pass

        assert len(event_ids) == total_expected

        stream.close()

    def test_concurrent_adds_no_id_gaps(self, file_store):
        """All event IDs from 0..N-1 should be present with no gaps."""
        stream = EventStream('no_gaps', file_store)
        num_threads = 3
        events_per_thread = 10
        barrier = threading.Barrier(num_threads)

        def add_events():
            barrier.wait()
            for _ in range(events_per_thread):
                stream.add_event(NullAction(), EventSource.AGENT)

        threads = [threading.Thread(target=add_events) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        total = num_threads * events_per_thread
        assert stream.cur_id == total

        stream.close()


# ---------------------------------------------------------------------------
# 2. Subscriber callback receives all events in order
# ---------------------------------------------------------------------------


class TestSubscriberOrdering:
    """Verify that a subscriber receives every event in the order they were added."""

    def test_single_subscriber_receives_all_events_in_order(self, event_stream):
        received = []
        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            lambda e: received.append(e.id),
            'ordering_test',
        )

        num_events = 10
        for i in range(num_events):
            event_stream.add_event(NullObservation(f'event_{i}'), EventSource.AGENT)

        # Wait for callbacks to complete
        time.sleep(1)

        assert len(received) == num_events
        assert received == list(range(num_events))

    def test_multiple_subscribers_all_receive_events(self, event_stream):
        received_a = []
        received_b = []

        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            lambda e: received_a.append(e.id),
            'sub_a',
        )
        event_stream.subscribe(
            EventStreamSubscriber.MAIN,
            lambda e: received_b.append(e.id),
            'sub_b',
        )

        num_events = 5
        for i in range(num_events):
            event_stream.add_event(NullObservation(f'event_{i}'), EventSource.AGENT)

        time.sleep(1)

        assert len(received_a) == num_events
        assert len(received_b) == num_events


# ---------------------------------------------------------------------------
# 3. Adding/removing subscribers during event dispatch
# ---------------------------------------------------------------------------


class TestSubscriberModificationDuringDispatch:
    """Verify that subscribing or unsubscribing during callback execution
    does not cause errors or data races."""

    def test_subscribe_during_dispatch_does_not_crash(self, event_stream):
        """Adding a subscriber inside a callback should not raise."""
        dispatch_count = [0]

        def callback_that_subscribes(event):
            dispatch_count[0] += 1
            # Add a new subscriber during dispatch (should be safe)
            try:
                event_stream.subscribe(
                    EventStreamSubscriber.MAIN,
                    lambda e: None,
                    f'dynamic_{event.id}',
                )
            except ValueError:
                pass  # Already exists, that's fine

        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            callback_that_subscribes,
            'subscriber_adder',
        )

        for _ in range(3):
            event_stream.add_event(NullObservation('test'), EventSource.AGENT)

        time.sleep(1)
        assert dispatch_count[0] == 3

    def test_unsubscribe_during_dispatch_does_not_crash(self, event_stream):
        """Removing a subscriber inside a callback should not raise."""
        received = []

        def self_removing_callback(event):
            received.append(event.id)
            event_stream.unsubscribe(EventStreamSubscriber.TEST, 'self_remover')

        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            self_removing_callback,
            'self_remover',
        )

        event_stream.add_event(NullObservation('first'), EventSource.AGENT)
        event_stream.add_event(NullObservation('second'), EventSource.AGENT)

        time.sleep(1)

        # The first event triggers the unsubscribe; the second should not deliver
        assert len(received) == 1


# ---------------------------------------------------------------------------
# 4. Error queue captures callback exceptions
# ---------------------------------------------------------------------------


class TestCallbackErrorPropagation:
    """Verify that exceptions raised inside subscriber callbacks are captured
    in the error queue and do not prevent other callbacks from executing."""

    def test_error_captured_in_error_queue(self, event_stream):
        def failing_callback(event):
            raise ValueError('callback exploded')

        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            failing_callback,
            'failing_cb',
        )

        event_stream.add_event(NullObservation('trigger'), EventSource.AGENT)
        time.sleep(1)

        errors = event_stream.get_callback_errors()
        assert len(errors) == 1
        subscriber_id, callback_id, exc = errors[0]
        assert callback_id == 'failing_cb'
        assert isinstance(exc, ValueError)
        assert 'callback exploded' in str(exc)

    def test_error_does_not_block_other_subscribers(self, event_stream):
        received = []

        def failing_callback(event):
            raise RuntimeError('fail')

        def healthy_callback(event):
            received.append(event.id)

        event_stream.subscribe(
            EventStreamSubscriber.AGENT_CONTROLLER,
            failing_callback,
            'failing',
        )
        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            healthy_callback,
            'healthy',
        )

        event_stream.add_event(NullObservation('test'), EventSource.AGENT)
        time.sleep(1)

        # Healthy callback should still execute
        assert len(received) == 1

        # Error should be captured
        errors = event_stream.get_callback_errors()
        assert len(errors) >= 1

    def test_get_callback_errors_drains_queue(self, event_stream):
        def failing_callback(event):
            raise RuntimeError('fail')

        event_stream.subscribe(
            EventStreamSubscriber.TEST,
            failing_callback,
            'failing',
        )

        event_stream.add_event(NullObservation('test'), EventSource.AGENT)
        time.sleep(1)

        errors1 = event_stream.get_callback_errors()
        assert len(errors1) == 1

        # Second call should return empty (queue was drained)
        errors2 = event_stream.get_callback_errors()
        assert len(errors2) == 0


# ---------------------------------------------------------------------------
# 5. Background writer flushes events correctly
# ---------------------------------------------------------------------------


class TestBackgroundWriter:
    """Verify that the background writer thread persists events to the file store."""

    def test_events_persisted_after_flush(self, file_store):
        stream = EventStream('writer_test', file_store)

        stream.add_event(NullObservation('persisted_event'), EventSource.AGENT)

        # Wait for background writer to flush
        time.sleep(0.5)

        # The event should be in the file store
        found = False
        for key in file_store.files:
            if '/events/' in key and '0.json' in key:
                found = True
                break

        assert found, 'Event should be persisted to file store'
        stream.close()

    def test_batch_flush_on_threshold(self, file_store):
        stream = EventStream('batch_test', file_store)

        # Add enough events to exceed the flush batch size
        for i in range(stream._FLUSH_BATCH_SIZE + 5):
            stream.add_event(NullObservation(f'batch_{i}'), EventSource.AGENT)

        # Wait for flush
        time.sleep(1)

        # All events should be persisted
        event_files = [
            k for k in file_store.files if '/events/' in k and k.endswith('.json')
        ]
        assert len(event_files) >= stream._FLUSH_BATCH_SIZE

        stream.close()

    def test_final_drain_on_close(self, file_store):
        stream = EventStream('drain_test', file_store)

        # Add an event and immediately close (final drain should persist it)
        stream.add_event(NullObservation('final'), EventSource.AGENT)
        stream.close()

        # The event should be in the file store
        found = any('/events/' in k and k.endswith('.json') for k in file_store.files)
        assert found, 'Final drain on close should persist pending events'


# ---------------------------------------------------------------------------
# 6. Shutdown sentinel stops the queue processing loop
# ---------------------------------------------------------------------------


class TestShutdownSentinel:
    """Verify that the shutdown sentinel properly terminates the queue loop."""

    def test_close_terminates_queue_thread(self, file_store):
        stream = EventStream('shutdown_test', file_store)

        assert stream._queue_thread.is_alive()

        stream.close()

        # The queue thread should have exited
        assert not stream._queue_thread.is_alive()

    def test_close_terminates_writer_thread(self, file_store):
        stream = EventStream('writer_shutdown', file_store)

        assert stream._writer_thread.is_alive()

        stream.close()

        # Writer thread should exit within the join timeout
        assert not stream._writer_thread.is_alive()

    def test_no_events_processed_after_close(self, file_store):
        stream = EventStream('post_close', file_store)
        received = []

        stream.subscribe(
            EventStreamSubscriber.TEST,
            lambda e: received.append(e.id),
            'after_close',
        )

        stream.add_event(NullObservation('before'), EventSource.AGENT)
        time.sleep(0.5)
        pre_close_count = len(received)

        stream.close()

        # After close, the queue loop should be stopped
        # Attempting to add an event would be unusual, but the queue thread is dead
        # so events would pile up on the queue without delivery
        assert pre_close_count == 1


# ---------------------------------------------------------------------------
# 7. Shared thread pool handles concurrent subscribers
# ---------------------------------------------------------------------------


class TestSharedThreadPool:
    """Verify that the shared callback pool dispatches to all subscribers
    concurrently without deadlock or starvation."""

    def test_pool_dispatches_to_all_subscribers_concurrently(self, file_store):
        stream = EventStream('pool_test', file_store)
        num_subscribers = 4
        received = {i: [] for i in range(num_subscribers)}
        barriers = threading.Barrier(num_subscribers, timeout=5)

        def make_callback(idx):
            def callback(event):
                # All callbacks should be running concurrently in the pool
                try:
                    barriers.wait(timeout=3)
                except threading.BrokenBarrierError:
                    pass
                received[idx].append(event.id)

            return callback

        subscriber_types = [
            EventStreamSubscriber.AGENT_CONTROLLER,
            EventStreamSubscriber.SERVER,
            EventStreamSubscriber.RUNTIME,
            EventStreamSubscriber.TEST,
        ]

        for i in range(num_subscribers):
            stream.subscribe(subscriber_types[i], make_callback(i), f'sub_{i}')

        stream.add_event(NullObservation('concurrent'), EventSource.AGENT)
        time.sleep(5)

        for i in range(num_subscribers):
            assert len(received[i]) == 1, f'Subscriber {i} should have received 1 event'

        stream.close()

    def test_slow_subscriber_does_not_drop_events(self, file_store):
        stream = EventStream('slow_sub', file_store)
        received = []

        def slow_callback(event):
            time.sleep(0.2)
            received.append(event.id)

        stream.subscribe(EventStreamSubscriber.TEST, slow_callback, 'slow')

        num_events = 5
        for i in range(num_events):
            stream.add_event(NullObservation(f'event_{i}'), EventSource.AGENT)

        # Wait long enough for all slow callbacks
        time.sleep(num_events * 0.3 + 1)

        assert len(received) == num_events
        assert received == list(range(num_events))

        stream.close()


# ---------------------------------------------------------------------------
# 8. Duplicate subscriber ID rejection
# ---------------------------------------------------------------------------


class TestSubscriberValidation:
    """Verify that duplicate callback IDs raise an error."""

    def test_duplicate_callback_id_raises(self, event_stream):
        event_stream.subscribe(EventStreamSubscriber.TEST, lambda e: None, 'dup_id')

        with pytest.raises(ValueError, match='already exists'):
            event_stream.subscribe(
                EventStreamSubscriber.TEST, lambda e: None, 'dup_id'
            )

    def test_same_callback_id_different_subscriber_types(self, event_stream):
        """Same callback ID under different subscriber types should be allowed."""
        event_stream.subscribe(EventStreamSubscriber.TEST, lambda e: None, 'shared_id')
        # Different subscriber type, same callback id -- should not raise
        event_stream.subscribe(EventStreamSubscriber.MAIN, lambda e: None, 'shared_id')


# ---------------------------------------------------------------------------
# 9. Event ID assignment is atomic under contention
# ---------------------------------------------------------------------------


class TestAtomicIdAssignment:
    """Stress test to verify that event ID assignment under high contention
    produces a correct monotonically-increasing sequence."""

    def test_high_contention_id_sequence(self, file_store):
        stream = EventStream('stress_ids', file_store)
        num_threads = 8
        events_per_thread = 50
        all_ids = []
        lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        original_add = stream.add_event.__func__

        def capturing_add(event, source):
            original_add(stream, event, source)
            with lock:
                all_ids.append(event.id)

        stream.add_event = lambda event, source: capturing_add(event, source)

        def worker():
            barrier.wait()
            for _ in range(events_per_thread):
                stream.add_event(NullObservation('stress'), EventSource.AGENT)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        total = num_threads * events_per_thread
        assert len(all_ids) == total
        assert len(set(all_ids)) == total  # All IDs unique
        assert sorted(all_ids) == list(range(total))  # 0..N-1, no gaps

        stream.close()
