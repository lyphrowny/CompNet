import attrs
from collections.abc import Iterable
from typing import Literal, Protocol
import logging
from .stream import PacketQueue, Packet, EOT
from .utils import get_logger

from .go_back_n import Sender as GBN_Sender, Reciever as GBN_Reciever
from .selective_repeat import Sender as SR_Sender, Reciever as SR_Reciever
import enum
from threading import Thread
import time


def _handle_floats(
    float_or_tuple: float | tuple[float, float],
) -> tuple[float, float]:
    if not isinstance(float_or_tuple, Iterable):
        return float_or_tuple, float_or_tuple
    return float_or_tuple


def config_streams(
    loss_probability: float | tuple[float, float],
    latency: float | tuple[float, float],
) -> tuple[PacketQueue, PacketQueue]:
    s_loss_p, r_loss_p = _handle_floats(loss_probability)
    s_latency, r_latency = _handle_floats(latency)

    return PacketQueue(s_loss_p, s_latency), PacketQueue(r_loss_p, r_latency)


class SenderProto(Protocol):
    @property
    def n_sent(self) -> int: ...

    def run(self) -> None: ...


class RecieverProto(Protocol):
    @property
    def n_recieved(self) -> int: ...

    def run(self) -> None: ...


class LowProtoEnum(enum.StrEnum):
    GBN = enum.auto()
    SR = enum.auto()


@attrs.define
class HighNetProtocol:
    low_proto: LowProtoEnum
    window_size: int
    message: str
    sender_timeout: float
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    sender: SenderProto = attrs.field(init=False)
    reciever: RecieverProto = attrs.field(init=False)
    log: logging.Logger = attrs.field(init=False)
    transmission_time: float = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.log = get_logger("H")
        match self.low_proto:
            case LowProtoEnum.GBN:
                sender_cls = GBN_Sender
                reciever_cls = GBN_Reciever
            case LowProtoEnum.SR:
                sender_cls = SR_Sender
                reciever_cls = SR_Reciever
        self.sender = sender_cls(
            s_to_r_stream=self.s_to_r_stream,
            r_to_s_stream=self.r_to_s_stream,
            message=self.message,
            window_size=self.window_size,
            timeout=self.sender_timeout,
        )
        self.reciever = reciever_cls(
            s_to_r_stream=self.s_to_r_stream,
            r_to_s_stream=self.r_to_s_stream,
            window_size=self.window_size,
        )

    def start_transmission(self):
        s_th = Thread(target=self.sender.run)
        r_th = Thread(target=self.reciever.run)

        start_time = time.monotonic()
        s_th.start()
        r_th.start()

        s_th.join()
        self.transmission_time = time.monotonic() - start_time
        self._close_connection()
        r_th.join()

    def _close_connection(self):
        """Emulate no_loss closing connection with no ack from reciever."""
        self.log.debug("Closing connection!")
        orig_loss = self.s_to_r_stream.loss_probability
        self.s_to_r_stream.loss_probability = 0
        self.s_to_r_stream.send(Packet(seq_num=EOT, payload="S"))
        self.s_to_r_stream.loss_probability = orig_loss
        self.log.debug("Connection closed!")
