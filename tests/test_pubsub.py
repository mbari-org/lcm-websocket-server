"""
Tests for LCMObserver and LCMRepublisher.
"""

import asyncio
import queue
import threading
import time

import pytest
from compas_lcmtypes.stdlcm import header_t

from lcm_websocket_server.lib.lcm_utils.pubsub import (
    MAX_CHANNEL_REGEX_LENGTH,
    OBSERVER_QUEUE_MAXSIZE,
    InvalidChannelRegex,
    LCMObserver,
    LCMRepublisher,
)
from tests.conftest import make_header_t

# ---------------------------------------------------------------------------
# LCMObserver: queue bounds and drop-oldest
# ---------------------------------------------------------------------------

class TestLCMObserverBoundedQueue:
    def test_default_maxsize_is_observer_queue_maxsize(self):
        obs = LCMObserver()
        assert obs._maxsize == OBSERVER_QUEUE_MAXSIZE

    def test_custom_maxsize_is_respected(self):
        obs = LCMObserver(queue_maxsize=4)
        assert obs._maxsize == 4

    def test_queue_accepts_messages_up_to_maxsize(self):
        obs = LCMObserver(queue_maxsize=4)
        for i in range(4):
            obs.handle(("CH", make_header_t(sequence=i)))
        assert len(obs) == 4

    def test_drop_oldest_when_full(self):
        """When the queue is full the oldest entry is evicted, not the newest."""
        obs = LCMObserver(queue_maxsize=3)

        for i in range(3):
            obs.handle(("CH", make_header_t(sequence=i)))  # queue: [0, 1, 2]

        # Overflow with a new message: oldest (seq=0) should be dropped.
        obs.handle(("CH", make_header_t(sequence=99)))  # queue: [1, 2, 99]

        assert len(obs) == 3

        sequences = []
        while True:
            try:
                _, data = obs.get_nowait()
                sequences.append(header_t.decode(data).sequence)
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
        assert len(obs) == maxsize

        sequences = []
        while True:
            try:
                _, data = obs.get_nowait()
                sequences.append(header_t.decode(data).sequence)
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

    def test_invalid_regex_raises(self):
        with pytest.raises(InvalidChannelRegex):
            LCMObserver(channel_regex="[invalid")

    def test_overlong_regex_raises(self):
        with pytest.raises(InvalidChannelRegex):
            LCMObserver(channel_regex="A" * (MAX_CHANNEL_REGEX_LENGTH + 1))


# ---------------------------------------------------------------------------
# LCMRepublisher: dispatch and subscription management
# ---------------------------------------------------------------------------

class TestLCMRepublisher:
    def test_dispatches_to_matching_observer(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(channel_regex="SENSOR")
        r.subscribe(obs)

        r.inject("SENSOR", make_header_t(sequence=7))

        ch, data = obs.get_nowait()
        assert ch == "SENSOR"
        assert header_t.decode(data).sequence == 7

    def test_does_not_dispatch_to_non_matching_observer(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(channel_regex="IMU")
        r.subscribe(obs)

        r.inject("SENSOR", make_header_t())

        with pytest.raises(queue.Empty):
            obs.get_nowait()

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

        _, data1 = obs1.get_nowait()
        _, data2 = obs2.get_nowait()
        _, data3 = obs3.get_nowait()

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
            obs.get_nowait()

    def test_multiple_injects_delivered_in_order(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver(queue_maxsize=10)
        r.subscribe(obs)

        for i in range(5):
            r.inject("CH", make_header_t(sequence=i))

        sequences = []
        for _ in range(5):
            _, data = obs.get_nowait()
            sequences.append(header_t.decode(data).sequence)

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
                except Exception as exc:  # noqa: BLE001 - collected and asserted below
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

        _ch, data = obs.get_nowait()
        assert header_t.decode(data).sequence == 42


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLCMRepublisherLifecycle:
    def test_stop_returns_promptly_on_quiet_bus(self):
        """stop() must not hang waiting on a blocking lcm.handle() call."""
        rp = LCMRepublisher("LWS_TEST_QUIET_CHANNEL_NOBODY_PUBLISHES")
        rp.start()
        t0 = time.monotonic()
        rp.stop()
        assert time.monotonic() - t0 < 2.0


# ---------------------------------------------------------------------------
# LCMObserver: conflation (latest per channel)
# ---------------------------------------------------------------------------

class TestLCMObserverConflate:
    def test_keeps_only_latest_per_channel(self):
        obs = LCMObserver(conflate=True)
        for i in range(10):
            obs.handle(("A", make_header_t(sequence=i)))
        obs.handle(("B", make_header_t(sequence=100)))
        assert len(obs) == 2
        events = obs.drain()
        assert [(ch, header_t.decode(d).sequence) for ch, d in events] == [("A", 9), ("B", 100)]

    def test_busy_channel_cannot_evict_quiet_channel(self):
        obs = LCMObserver(conflate=True, queue_maxsize=2)
        obs.handle(("QUIET", make_header_t(sequence=1)))
        for i in range(1000):
            obs.handle(("BUSY", make_header_t(sequence=i)))
        channels = [ch for ch, _ in obs.drain()]
        assert channels == ["QUIET", "BUSY"]
        assert obs.drop_count == 0

    def test_get_nowait_serves_in_insertion_order(self):
        obs = LCMObserver(conflate=True)
        obs.handle(("A", make_header_t(sequence=1)))
        obs.handle(("B", make_header_t(sequence=2)))
        obs.handle(("A", make_header_t(sequence=3)))
        assert obs.get_nowait()[0] == "A"
        assert obs.get_nowait()[0] == "B"
        with pytest.raises(queue.Empty):
            obs.get_nowait()


# ---------------------------------------------------------------------------
# LCMObserver: asyncio consumer
# ---------------------------------------------------------------------------

class TestLCMObserverAsync:
    async def test_get_returns_buffered_event_immediately(self):
        obs = LCMObserver()
        obs.handle(("CH", b"x"))
        assert await asyncio.wait_for(obs.get(), 1.0) == ("CH", b"x")

    async def test_get_wakes_on_event_from_other_thread(self):
        obs = LCMObserver()
        threading.Timer(0.05, obs.handle, args=(("CH", b"x"),)).start()
        t0 = time.monotonic()
        assert await asyncio.wait_for(obs.get(), 1.0) == ("CH", b"x")
        assert time.monotonic() - t0 < 0.5

    @pytest.mark.parametrize("conflate", [False, True])
    async def test_no_lost_wakeups_under_concurrent_production(self, conflate):
        """A producer thread racing the consumer must never leave it waiting with data buffered."""
        n = 20_000
        obs = LCMObserver(queue_maxsize=n, conflate=conflate)

        def produce():
            for i in range(n):
                obs.handle((f"CH{i % 7}" if conflate else "CH", i.to_bytes(4, "big")))
                if i % 97 == 0:
                    time.sleep(0)  # yield to vary interleavings

        threading.Thread(target=produce, daemon=True).start()
        last = -1
        while last != n - 1:
            _, data = await asyncio.wait_for(obs.get(), 2.0)
            value = int.from_bytes(data, "big")
            if not conflate:
                assert value == last + 1  # FIFO, nothing dropped
            last = max(last, value)


# ---------------------------------------------------------------------------
# LCMRepublisher: routing cache, taps, fault isolation
# ---------------------------------------------------------------------------

class TestLCMRepublisherRouting:
    def test_subscriber_added_after_route_cached_receives_messages(self):
        r = LCMRepublisher(".*")
        first = LCMObserver(channel_regex="CH")
        r.subscribe(first)
        r.inject("CH", b"1")  # populates the route cache

        second = LCMObserver(channel_regex="C.*")
        r.subscribe(second)
        r.inject("CH", b"2")
        assert second.get_nowait() == ("CH", b"2")

    def test_unsubscribe_invalidates_route(self):
        r = LCMRepublisher(".*")
        obs = LCMObserver()
        r.subscribe(obs)
        r.inject("CH", b"1")
        r.unsubscribe(obs)
        r.inject("CH", b"2")
        assert obs.drain() == [("CH", b"1")]

    def test_regex_evaluated_once_per_channel(self):
        calls = []

        class CountingObserver(LCMObserver):
            def match(self, channel):
                calls.append(channel)
                return super().match(channel)

        r = LCMRepublisher(".*")
        r.subscribe(CountingObserver(queue_maxsize=1000))
        for _ in range(100):
            r.inject("CH", b"x")
        assert calls == ["CH"]

    def test_tap_called_with_arrival_timestamp(self):
        r = LCMRepublisher(".*")
        seen = []
        r.add_tap(lambda ch, data, ts: seen.append((ch, data, ts)))
        before = time.monotonic_ns()
        r.inject("CH", b"x")
        assert seen and seen[0][:2] == ("CH", b"x") and seen[0][2] >= before

    def test_exceptions_do_not_escape_handle(self):
        """An exception on the LCM thread would kill it; _handle must contain it."""
        r = LCMRepublisher(".*")

        def bad_tap(ch, data, ts):
            raise RuntimeError("boom")

        r.add_tap(bad_tap)
        r.inject("CH", b"x")  # must not raise
