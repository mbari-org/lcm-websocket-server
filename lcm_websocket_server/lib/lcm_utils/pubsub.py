"""
LCM pub/sub utilities.
"""

import queue
import re

from lcmutils import LCMDaemon

from lcm_websocket_server.lib.log import LogMixin


class LCMObserver:
    """
    Observer for an LCMObservable. Puts received events in a thread-safe queue.
    """
    def __init__(self, channel_regex: str = ".*"):
        self._queue = queue.Queue()
        self._channel_regex = channel_regex
    
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

    def handle(self, event: tuple[str, bytes]) -> None:
        """
        Handle an LCM event.
        
        Args:
            event (tuple[str, bytes]): The LCM event (channel, data)
        """
        self._queue.put(event)
    
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
    
    def subscribe(self, subscriber: LCMObserver) -> None:
        """
        Subscribe a subscriber to this observable.
        
        Args:
            subscriber (LCMObserver): The subscriber to subscribe.
        """
        self._subscribers.append(subscriber)
    
    def unsubscribe(self, subscriber: LCMObserver) -> None:
        """
        Unsubscribe a subscriber from this observable.
        
        Args:
            subscriber (LCMObserver): The subscriber to unsubscribe.
        """
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
        for subscriber in self._subscribers:
            if subscriber.match(channel):
                subscriber.handle((channel, data))
