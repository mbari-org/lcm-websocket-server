"""
LCM pub/sub utilities.
"""

import logging
import queue
import re
import threading
from time import monotonic_ns

from lcmutils import LCMDaemon

from lcm_websocket_server.lib.log import LogMixin

_logger = logging.getLogger(__name__)

OBSERVER_QUEUE_MAXSIZE = 64


class LCMObserver:
    """
    Observer for an LCMObservable. Puts received events in a thread-safe queue.

    The internal queue is bounded (default: OBSERVER_QUEUE_MAXSIZE). When the
    queue is full the oldest entry is dropped so the consumer always sees the
    most recent data rather than accumulating unbounded backlog.
    """
    def __init__(self, channel_regex: str = ".*", queue_maxsize: int = OBSERVER_QUEUE_MAXSIZE):
        self._queue = queue.Queue(maxsize=queue_maxsize)
        self._channel_regex = channel_regex
        self._drop_count: int = 0

    def match(self, channel: str) -> bool:
        """
        Check if the observer matches a given channel.

        Args:
            channel (str): Channel name

        Returns:
            bool: True if the observer matches the channel, False otherwise.
        """
        try:
            return re.fullmatch(self._channel_regex, channel) is not None
        except re.error:
            return False

    def _put(self, event) -> None:
        """
        Put an event into the queue, dropping the oldest entry if the queue is full.

        Called from the LCM thread (single producer), so the get/put pair here
        is safe: no other producer can race between the two calls.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()
                # Keep the unfinished-task count balanced so queue.join() works
                # if anyone ever uses it.
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                pass  # extremely unlikely second race; skip the message

            self._drop_count += 1
            # Log on the first drop and then at each power-of-ten threshold so
            # the operator is alerted immediately but not spammed on a fast stream.
            if self._drop_count == 1 or self._drop_count % 10 ** len(str(self._drop_count - 1)) == 0:
                channel = event[0] if event else "unknown"
                _logger.warning(
                    "Observer (channel regex %r) dropped oldest message on channel %r "
                    "(queue full at maxsize=%d, total dropped: %d). "
                    "Consider increasing --queue-size or reducing the publish rate.",
                    self._channel_regex,
                    channel,
                    self._queue.maxsize,
                    self._drop_count,
                )

    def handle(self, event: tuple[str, bytes]) -> None:
        """
        Handle an LCM event.

        Args:
            event (tuple[str, bytes]): The LCM event (channel, data)
        """
        self._put(event)

    def get(self, *args, **kwargs) -> tuple[str, bytes]:
        """
        Get the next event from the queue.

        Returns:
            tuple[str, bytes]: The next event (channel, data)
        """
        return self._queue.get(*args, **kwargs)

    def task_done(self) -> None:
        """
        Indicate that a formerly enqueued event (i.e., the last call to `LCMObserver.get`) is complete.
        """
        self._queue.task_done()


class LCMTimedObserver(LCMObserver):
    """
    Observer variant that captures an arrival timestamp for each event.
    """

    def handle(self, event: tuple[str, bytes]) -> None:
        channel, data = event
        self._put((channel, data, monotonic_ns()))

    def get(self, *args, **kwargs) -> tuple[str, bytes, int]:
        return self._queue.get(*args, **kwargs)


class LCMRepublisher(LogMixin):
    """
    Subscribes to an LCM channel in a background thread and republishes events to subscribers.
    """
    def __init__(self, channel: str):
        """
        Args:
            channel (str): The LCM channel regex to subscribe to.
        """
        self._channel = channel

        self._daemon = LCMDaemon()
        self._daemon.subscribe(self._channel)(self._handle)

        self._subscribers: list[LCMObserver] = []
        # Protects _subscribers against concurrent mutation (asyncio thread)
        # vs iteration (_handle, LCM thread).
        self._subscribers_lock = threading.Lock()

    def subscribe(self, subscriber: LCMObserver) -> None:
        """
        Subscribe a subscriber to this observable.

        Args:
            subscriber (LCMObserver): The subscriber to subscribe.
        """
        with self._subscribers_lock:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: LCMObserver) -> None:
        """
        Unsubscribe a subscriber from this observable.

        Args:
            subscriber (LCMObserver): The subscriber to unsubscribe.
        """
        with self._subscribers_lock:
            self._subscribers.remove(subscriber)

    def start(self) -> None:
        """
        Start the LCM republisher asynchronously.
        """
        self._daemon.start()

    def stop(self) -> None:
        """
        Stop the LCM republisher.
        """
        self._daemon.stop()

    @property
    def subscriber_count(self) -> int:
        with self._subscribers_lock:
            return len(self._subscribers)

    def inject(self, channel: str, data: bytes) -> None:
        """
        Inject a virtual message into the republisher (not from LCM).

        This is useful for creating "virtual channels" that don't come from LCM
        but should be distributed to subscribers as if they were.

        Args:
            channel (str): The virtual channel name
            data (bytes): The message data
        """
        self._handle(channel, data)

    def _handle(self, channel: str, data: bytes):
        """
        Handle an LCM event.

        Args:
            channel (str): The LCM channel
            data (bytes): The LCM data
        """
        # Take a snapshot under the lock so we don't hold the lock during
        # per-subscriber dispatch (which calls queue.put).
        with self._subscribers_lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            if subscriber.match(channel):
                subscriber.handle((channel, data))
