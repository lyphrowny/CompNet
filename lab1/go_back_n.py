from collections.abc import Iterable
from typing import Literal
import attrs
import random
import queue
import time
from threading import Thread
from functools import wraps

import logging

log_level = logging.DEBUG


def get_logger(prefix: str):
    logger = logging.getLogger(prefix)

    # if already created and configured
    if logger.hasHandlers():
        return logger

    logger.setLevel(log_level)

    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    formatter = logging.Formatter(f"{prefix} - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


rlog = get_logger("r")
slog = get_logger("s")
plog = get_logger("p")


STOP_TRANSFER = "\x00"
# end of transmission
EOT = -1


@attrs.define
class Packet:
    seq_num: int
    payload: str


@attrs.define
class PacketQueue:
    loss_probability: float = 0.3
    latency: float = 0.1  # in sec
    _queue: queue.Queue[Packet] = attrs.field(init=False, factory=queue.Queue)

    def send(self, packet: Packet):
        # emulate network latency
        time.sleep(self.latency)
        if random.random() < self.loss_probability:
            plog.debug(f"{packet} was lost!")
        else:
            self._queue.put(packet)

    def recieve(self):
        return self._queue.get()

    def __bool__(self) -> bool:
        return not self._queue.empty()


def no_loss(func, stream: PacketQueue):
    @wraps(func)
    def wrapper(self, *a, **kw) -> None:
        orig_loss = stream.loss_probability
        stream.loss_probability = 0
        func(self, *a, **kw)
        stream.loss_probability = orig_loss

    return wrapper


@attrs.define
class Sender:
    """Go-Back-N sender

    Say we have a message with len 10. Window size is 3.
    Split into batches: [1, 2, 3], [4, 5, 6], [7, 8, 9], [10]
    Then, the seq_nums will be [0, 1, 2], [0, 1, 2], [0, 1, 2], [0]
    So, `seq_num` is the position in a batch. Can be calculated as
    `message_pos % window_size`

    The window is used solely to send `window_size` packets not waiting for ACK
    from reciever. We send `window_size` packets first. For each ACK'ed packet,
    move the window (`left_bound += 1`) but don't update `m_pos`. `m_pos` points
    at the not yet sent packet
    left bound     left bound
    v              v
    |0 1 2| 3 -> 0 |1 2 3|
       ^            ^
       message pos  message pos

    On each iteration check for packet from reciever. Got one? Move the `left_bound`
    Now we can send another packet (unless we sent all the packetsand waiting for
    their ACKs). On each send update `m_pos`.
    If timed out, resend the message from `left_bound`.
    """

    sender_to_reciever_ch: PacketQueue
    reciever_to_sender_ch: PacketQueue
    message: str
    window_size: int = 10
    timeout: float = 1.0
    n_sent: int = 0
    num_packets: int = attrs.field(init=False)

    def __attrs_post_init__(self):
        # self.message += STOP_TRANSFER
        self.num_packets = len(self.message)

    def run(self):
        left_bound = 0  # left boundary of the window
        m_pos = 0  # message position
        last_send_time = 0
        while left_bound < self.num_packets:
            if self.reciever_to_sender_ch:
                packet = self.reciever_to_sender_ch.recieve()
                slog.debug(f"Recieved ACK for {packet.seq_num}")
                slog.debug(
                    f"Window before update {self.message[left_bound : left_bound + self.window_size]!r}"
                )
                if packet.seq_num != (
                    expected_seq_num := (left_bound + 1) % (self.window_size + 1)
                ):
                    slog.debug(
                        (
                            f"Recieved ACK {packet.seq_num} doesn't match "
                            f"the expected seq_num {expected_seq_num}"
                        )
                    )
                    expected_seq_num = (left_bound) % (self.window_size + 1)
                    diff = packet.seq_num - expected_seq_num
                    if packet.seq_num < expected_seq_num:
                        diff = (
                            packet.seq_num + (self.window_size + 1) - expected_seq_num
                        )
                    left_bound += diff
                    slog.debug(f"{m_pos = }, {self.message[m_pos : m_pos + 1]!r}")
                    # m_pos -= diff
                    # m_pos = left_bound
                else:
                    left_bound += 1
                slog.debug(
                    f"Window after update {self.message[left_bound : left_bound + self.window_size]!r}"
                )

            if m_pos < min(left_bound + self.window_size, self.num_packets):
                packet = Packet(
                    seq_num=m_pos % (self.window_size + 1),
                    payload=self.message[m_pos],
                )
                slog.debug(f"Sent {packet}")
                self.sender_to_reciever_ch.send(packet)
                m_pos += 1
                self.n_sent += 1
                last_send_time = time.monotonic()

            if time.monotonic() - last_send_time > self.timeout:
                slog.debug(
                    f"Timed out, resending from {left_bound} ({self.message[left_bound]})"
                )
                m_pos = left_bound

    #     self.handle_eot()

    # def handle_eot(self):
    #     eot_packet = Packet(seq_num=EOT, payload="S")
    #     # send eot_packets until EOT ACK is recieved
    #     while True:
    #         slog.debug("Sending EOT")
    #         self.sender_to_reciever_ch.send(eot_packet)

    #         if self.reciever_to_sender_ch:
    #             packet = self.reciever_to_sender_ch.recieve()
    #             if packet.seq_num == EOT:
    #                 slog.debug("Got EOT ACK")
    #                 break
    #             else:
    #                 slog.debug(f"Got some ACK {packet.seq_num}")
    #         time.sleep(self.timeout)


@attrs.define
class Reciever:
    """Go-Back-N reciever

    Recieve packets and send ACKs for recieved ones
    """

    sender_to_reciever_ch: PacketQueue
    reciever_to_sender_ch: PacketQueue
    window_size: int = 10
    n_recieved: int = 0
    eot_disconnect_timeout: float = 2.0

    def run(self):
        expected_seq_num = 0
        message = ""

        n_right = 0
        n_wrong = 0

        while True:
            # will block until there is something to recieve
            packet = self.sender_to_reciever_ch.recieve()
            self.n_recieved += 1

            # transfer ended
            if packet.seq_num == EOT:
                break

            if packet.seq_num == expected_seq_num:
                n_right += 1
                rlog.debug(f"Recieved {packet}")
                message += packet.payload
                rlog.debug(
                    f"Sent ACK {expected_seq_num} Request {(expected_seq_num + 1) % (self.window_size + 1)}"
                )
                expected_seq_num = (expected_seq_num + 1) % (self.window_size + 1)
                # self.reciever_to_sender_ch.send(
                #     Packet(seq_num=expected_seq_num, payload="")
                # )
            else:
                n_wrong += 1
                rlog.debug(f"Ignoring {packet}: was expecting {expected_seq_num}")
                rlog.debug(f"Sending Request {expected_seq_num}")
            rlog.debug(f"Current message {message!r}")
            self.reciever_to_sender_ch.send(
                Packet(seq_num=expected_seq_num % (self.window_size + 1), payload="ACK")
            )

        # self.handle_eot()

        rlog.debug(f"Recieved message: {message}")
        rlog.debug(f"{n_right = }")
        rlog.debug(f"{n_wrong = }")

    # def handle_eot(self):
    #     eot_packet = Packet(seq_num=EOT, payload="R")
    #     resend = True
    #     last_send_time = 0
    #     while True:
    #         if resend:
    #             rlog.debug("Sending EOT ACK")
    #             self.reciever_to_sender_ch.send(eot_packet)
    #             resend = False
    #             last_send_time = time.monotonic()

    #         # we recieve? we send back!
    #         # this means, the sender didn't get our EOT ACK
    #         if self.sender_to_reciever_ch:
    #             packet = self.sender_to_reciever_ch.recieve()
    #             rlog.debug("Got EOT again")
    #             if packet.seq_num == EOT:
    #                 resend = True
    #             else:
    #                 rlog.debug(f"Expected EOT, got {packet.seq_num}")

    #         # probably, the sender got the ACK for the EOT packet
    #         if time.monotonic() - last_send_time > self.eot_disconnect_timeout:
    #             rlog.debug("EOT timeout")
    #             break


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


@attrs.define
class HighNetProtocol:
    low_proto: Literal["GBN", "SR"]
    window_size: int
    message: str
    reciever_timeout: float
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    sender: Sender = attrs.field(init=False)
    reciever: Reciever = attrs.field(init=False)
    log: logging.Logger = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.log = get_logger("H")
        match self.low_proto:
            case "GBN":
                sender_cls = Sender
                reciever_cls = Reciever
            case _:
                raise ValueError(f"No low proto named {self.low_proto} is imlemented!")
        self.sender = sender_cls(
            sender_to_reciever_ch=self.s_to_r_stream,
            reciever_to_sender_ch=self.r_to_s_stream,
            message=self.message,
            window_size=self.window_size,
            timeout=self.reciever_timeout,
        )
        self.reciever = reciever_cls(
            sender_to_reciever_ch=self.s_to_r_stream,
            reciever_to_sender_ch=self.r_to_s_stream,
            window_size=self.window_size,
        )

    def start_transmission(self):
        s_th = Thread(target=self.sender.run)
        r_th = Thread(target=self.reciever.run)

        s_th.start()
        r_th.start()

        s_th.join()
        self._close_connection()
        r_th.join()
        # threads = (
        #     Thread(target=self.sender.run),
        #     Thread(target=self.reciever.run),
        # )
        # for th in threads:
        #     th.start()
        # for th in threads:
        #     th.join()

    def _close_connection(self):
        """Emulate no_loss closing connection with no ack from reciever."""
        self.log.debug("Closing connection!")
        orig_loss = self.s_to_r_stream.loss_probability
        self.s_to_r_stream.loss_probability = 0
        self.s_to_r_stream.send(Packet(seq_num=EOT, payload="S"))
        self.s_to_r_stream.loss_probability = orig_loss
        self.log.debug("Connection closed!")


if __name__ == "__main__":
    s_to_r_stream, r_to_s_stream = config_streams(
        loss_probability=(0.2, 0.3),
        latency=0.05,
    )

    from string import ascii_lowercase

    msg = ascii_lowercase[:5]
    high_proto = HighNetProtocol(
        low_proto="GBN",
        window_size=3,
        message=msg,
        reciever_timeout=1,
        s_to_r_stream=s_to_r_stream,
        r_to_s_stream=r_to_s_stream,
    )

    high_proto.start_transmission()

    # latency = 0.05
    # loss_probability = 0.2
    # window_size = 3
    # # a channel from sender to reciever: sender puts, reciever gets
    # sender_to_reciever_ch = PacketQueue(
    #     loss_probability=loss_probability,
    #     latency=latency,
    # )
    # # a channel from reciever to sender: reciever puts, sender gets
    # reciever_to_sender_ch = PacketQueue(
    #     loss_probability=0.3,
    #     latency=latency,
    # )

    # from string import ascii_lowercase

    # msg = ascii_lowercase[:5]
    # sender = Sender(
    #     sender_to_reciever_ch,
    #     reciever_to_sender_ch,
    #     message=msg,
    #     window_size=window_size,
    # )
    # reciever = Reciever(
    #     sender_to_reciever_ch,
    #     reciever_to_sender_ch,
    #     window_size=window_size,
    # )

    # # threads = [Thread(target=sender.run), Thread(target=reciever.run)]

    # # for th in threads:
    # #     th.start()
    # # for th in threads:
    # #     th.join()

    # th_s = Thread(target=sender.run)
    # th_r = Thread(target=reciever.run)

    # th_s.start()
    # th_r.start()

    # th_s.join()
    # th_r.join()

    # print(f"{sender.n_sent = }")
    # print(f"{reciever.n_recieved = }")
