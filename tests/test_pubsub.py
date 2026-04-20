"""
Tests for LCMObserver, LCMTimedObserver, and LCMRepublisher.
"""

import queue
import threading
import time

import pytest
from stdlcm import header_t

from lcm_websocket_server.lib.lcm_utils.pubsub import (
    OBSERVER_QUEUE_MAXSIZE,
    LCMObserver,
    LCMRepublisher,
    LCMTimedObserver,
)

from tests.conftest import make_header_t


# ---------------------------------------------------------------------------
# LCMObserver: queue bounds and drop-oldest
# ---------------------------------------------------------------------------

class TestLCMObserverBoundedQueue:
    def test_default_maxsize_is_observer_queue_maxsize(self):
        obs = LCMObserver()
        assert obs._queue.maxsize == OBSERVER_QUEUE_MAXSIZE

    def test_custom_maxsize_is_respected(self):
        obs = LCMObserver(queue_maxsize=4)
        assert obs._queue.maxsize == 4

    def test_queue_accepts_messages_up_to_maxsize(self):
        obs = LCMObserver(queue_maxsize=4)
        for i in range(4):
            obs.handle(("CH", make_header_t(sequence=i)))
        assert obs._queue.qsize() == 4

    def test_drop_oldest_when_full(self):
        """When the queue is full the oldest entry is evicted, not the newest."""
        obs = LCMObserver(queue_maxsize=3)

        for i in range(3):
            obs.handle(("CH", make_header_t(sequence=i)))  # queue: [0, 1, 2]

        # Overflow with a new message: oldest (seq=0) should be dropped.
        obs.handle(("CH", make_header_t(sequence=99)))  # queue: [1, 2, 99]

        assert obs._queue.qsize() == 3

        sequences = []
        while True:
            try:
                _, data = obs.get(block=False)
                sequences.append(header_t.decode(data).sequence)
                obs.task_done()
            except queue.Empty:
                break

        assert sequences == [1, 2, 99], f"Expected [1, 2, 99], got {sequences}"

    def test_latest_message_always_present_after_many_overflows(self):
        """Under heavy overflow only the latest N messages survive."""
        maxsize = 4
        obs = LCMObserver(queue_maxsize=maxsize)

        total = 20
        for i in range(total):
            obs.handle(("CH", make_header_t(sequence=i)))

        # The queue should hold exactly the last `maxsize` messages.
        assert obs._queue.qsize() == maxsize

        sequences = []
        while True:
            try:
                _, data = obs.get(block=False)
                sequences.append(header_t.decode(data).sequence)
                obs.task_done()
            except queue.Empty:
                break

        assert sequences == list(range(total - maxsize, total))

    def test_drop_count_increments_on_overflow(self):
        obs = LCMObserver(queue_maxsize=2)
        for i in range(5):
            obs.handle(("CH", make_header_t(sequence=i)))
        assert obs._drop_count == 3

    def test_drop_warning_emitted_on_first_overflow(self, caplog):
        import logging
        obs = LCMObserver(queue_maxsize=1)
        with caplog.at_level(logging.WARNING, logger="lcm_websocket_server.lib.lcm_utils.pubsub"):
            obs.handle(("CH", make_header_t()))  # fills queue
            obs.handle(("CH", make_header_t()))  # first drop → warning
        assert any("dropped" in r.message for r in caplog.records)
        assert any("queue-size" in r.message for r in caplog.records)

    def test_drop_warning_rate_limited_to_powers_of_ten(self, caplog):
        """Warnings fire at drop counts 1, 10, 100, … not on every drop."""
        import logging
        obs = LCMObserver(queue_maxsize=1)
        obs.handle(("CH", make_header_t()))  # fills queue
        with caplog.at_level(logging.WARNING, logger="lcm_websocket_server.lib.lcm_utils.pubsub"):
            for _ in range(110):
                obs.handle(("CH", make_header_t()))
        # Drops 1–110 → warnings at 1, 10, 100 = 3 records
        drop_warnings = [r for r in caplog.records if "dropped" in r.message]
        assert len(drop_warnings) == 3

    def test_task_done_count_balanced_after_drop(self):
        """
        Each dropped item calls task_done() internally so queue.join() never
        deadlocks when the caller calls task_done() for the item it consumed.
        """
        obs = LCMObserver(queue_maxsize=2)
        for i in range(4):  # 2 overflow → 2 internal task_done() calls
            obs.handle(("CH", make_header_t(sequence=i)))

        # Consume both surviving items and call task_done().
        for _ in range(2):
            obs.get(block=False)
            obs.task_done()

        # join() should return immediately (all tasks done).
        done = threading.Event()
        t = threading.Thread(target=lambda: (obs._queue.join(), done.set()), daemon=True)
        t.start()
        t.join(timeout=1.0)
        assert done.is_set(), "queue.join() did not return — task_done() accounting is broken"


# ---------------------------------------------------------------------------
# LCMObserver: channel regex matching
# ---------------------------------------------------------------------------

class TestLCMObserverChannelMatching:
    def test_matches_exact_channel(self):
        obs = LCMObserver(channel_regex="SENSOR_DATA")
        assert obs.match("SENSOR_DATA") is True

    def test_matches_wildcard(self):
        obs = LCMObserver(channel_regex=".*")
        assert obs.match("ANYTHING") is True

    def test_does_not_match_partial_without_anchors(self):
        # fullmatch requires the regex to match the entire string
        obs = LCMObserver(channel_regex="SENSOR")
        assert obs.match("SENSOR_DATA") is False

    def test_matches_regex_pattern(self):
        obs = LCMObserver(channel_regex="CAM_.*")
        assert obs.match("CAM_FRONT") is True
        assert obs.match("CAM_REAR") is True
        assert obs.match("IMU") is False

    def test_invalid_regex_returns_false(self):
        obs = LCMObserver(channel_regex="[invalid")
        assert obs.match("anything") is False


# ---------------------------------------------------------------------------
# LCMTimedObserver
# ---------------------------------------------------------------------------

class TestLCMTimedObserver:
    def test_includes_monotonic_timestamp(self):
        obs = LCMTimedObserver()
        before = time.monotonic_ns()
        obs.handle(("CH", make_header_t()))
        after = time.monotonic_ns()

        _, _, ts = obs.get(block=False)
        obs.task_done()

        assert before <= ts <= after

    def test_drop_oldest_preserves_latest_timestamps(self):
        obs = LCMTimedObserver(queue_maxsize=2)
        for i in range(5):
            obs.handle(("CH", make_header_t(sequence=i)))
            time.sleep(0.001)  # Ensure distinct timestamps

        assert obs._queue.qsize() == 2

        items = []
        while True:
            try:
                items.append(obs.get(block=False))
                obs.task_done()
            except queue.Empty:
                break

        # Timestamps should be strictly increasing (oldest-first FIFO).
        timestamps = [item[2] for item in items]
        assert timestamps == sorted(timestamps)
        # And they should correspond to the last two messages.
        sequences = [header_t.decode(item[1]).sequence for item in items]
        assert sequences == [3, 4]


# ---------------------------------------------------------------------------
# LCMRepublisher: dispatch and subscription management
# ---------------------------------------------------------------------------

class TestLCMRepublisher:
    def test_dispatches_to_matching_observer(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(channel_regex="SENSOR")
        r.subscribe(obs)

        r.inject("SENSOR", make_header_t(sequence=7))

        ch, data = obs.get(block=False)
        assert ch == "SENSOR"
        assert header_t.decode(data).sequence == 7

    def test_does_not_dispatch_to_non_matching_observer(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(channel_regex="IMU")
        r.subscribe(obs)

        r.inject("SENSOR", make_header_t())

        with pytest.raises(queue.Empty):
            obs.get(block=False)

    def test_dispatches_same_data_object_to_all_matching_observers(self):
        """
        Multiple observers should receive the same bytes object (not a copy)
        so that EncodeCache identity checks work correctly.
        """
        r = LCMRepublisher(".*")
        obs1 = LCMObserver()
        obs2 = LCMObserver()
        obs3 = LCMObserver()
        r.subscribe(obs1)
        r.subscribe(obs2)
        r.subscribe(obs3)

        r.inject("CH", make_header_t())

        _, data1 = obs1.get(block=False)
        _, data2 = obs2.get(block=False)
        _, data3 = obs3.get(block=False)

        # All three must be the exact same Python object (identity, not equality).
        assert data1 is data2
        assert data2 is data3

    def test_unsubscribe_stops_delivery(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver()
        r.subscribe(obs)
        r.unsubscribe(obs)

        r.inject("CH", make_header_t())

        with pytest.raises(queue.Empty):
            obs.get(block=False)

    def test_multiple_injects_delivered_in_order(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(queue_maxsize=10)
        r.subscribe(obs)

        for i in range(5):
            r.inject("CH", make_header_t(sequence=i))

        sequences = []
        for _ in range(5):
            _, data = obs.get(block=False)
            sequences.append(header_t.decode(data).sequence)
            obs.task_done()

        assert sequences == list(range(5))


# ---------------------------------------------------------------------------
# Fix 4: Thread-safety of subscriber list
# ---------------------------------------------------------------------------

class TestRepublisherThreadSafety:
    def test_concurrent_subscribe_unsubscribe_during_dispatch(self):
        """
        Simulates the real scenario: LCM thread calls _handle() (reads subscriber
        list) while the asyncio thread calls subscribe()/unsubscribe() (mutates it).
        Should complete without any exception.
        """
        r = LCMRepublisher(".*")
        errors: list[Exception] = []
        data = make_header_t()

        def inject_loop():
            for _ in range(2000):
                try:
                    r.inject("CH", data)
                except Exception as exc:
                    errors.append(exc)

        injector = threading.Thread(target=inject_loop, daemon=True)
        injector.start()

        # While the injector runs, rapidly add and remove observers.
        observers = []
        for _ in range(100):
            obs = LCMObserver(queue_maxsize=4)
            r.subscribe(obs)
            observers.append(obs)

        for obs in observers:
            r.unsubscribe(obs)

        injector.join(timeout=5.0)
        assert not injector.is_alive(), "Injector thread hung"
        assert not errors, f"Exception in injector thread: {errors}"

    def test_subscribe_during_dispatch_does_not_lose_message(self):
        """
        A subscriber added just before inject() should receive the message.
        """
        r = LCMRepublisher(".*")
        obs = LCMObserver()
        r.subscribe(obs)
        r.inject("CH", make_header_t(sequence=42))

        ch, data = obs.get(block=False)
        assert header_t.decode(data).sequence == 42
