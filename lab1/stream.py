import logging
from typing import Protocol
import attrs
import queue
import time
import random

from .utils import get_logger


# end of transmission seq_num
EOT = -1


@attrs.define
class Packet:
    seq_num: int
    payload: str
    sent_at: int = attrs.field(
        default=0,
        # 123.456 sec but as int (123_456)
        # repr=lambda t: f"{int(t // 1e6 % 1e6):_}",
    )


@attrs.define
class PacketQueue:
    loss_probability: float = 0.3
    latency: float = 0.1  # in sec
    _queue: queue.Queue[Packet] = attrs.field(init=False, factory=queue.Queue)
    log: logging.Logger = attrs.field(init=False, default=get_logger("p"))

    def send(self, packet: Packet):
        # emulate network latency
        time.sleep(self.latency)
        if random.random() < self.loss_probability:
            self.log.debug(f"{packet} was lost!")
        else:
            self._queue.put(packet)

    def recieve(self):
        return self._queue.get()

    def __bool__(self) -> bool:
        return not self._queue.empty()
