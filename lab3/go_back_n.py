from collections.abc import Iterable, Sequence
from itertools import batched
from queue import Queue
import threading
import time

import attrs

from .stream import EOT, PacketQueue, Packet as LPacket, NIT
from .utils import get_logger
from .high_transfer import Packet, UID, Action

rlog = get_logger("r")
slog = get_logger("s")


@attrs.define
class Sender:
    """Go-Back-N sender

    Say we have a message with len 10. Window size is 3.
    Split into batches: [1, 2, 3], [4, 5, 6], [7, 8, 9], [10]
    The seq_mod should be > window_size, otherwise when all packets are lost,
    one cannot determine, whether the reciever lost all packets and requesting
    resending (Request 0), or all packets were delivered and the reciever
    requests a new batch (Request 0)
    seq_mod = win_size          vs.              seq_mod = win_size + 1
    0 -> ACK lost                                   0 -> ACK lost
    1 -> ACK lost                                   1 -> ACK lost
    2 -> ACK lost                                   2 -> ACK lost
    2(0) <- ACK 2 Request 0                         2(3) <- ACK 2 Request 3
    ^ should we send new batch? should we repeat?
            it's clear, that new batch is requested ^
    Then, the seq_nums will be [0, 1, 2, 3], [0, 1, 2, 3], [0, 1]
    So, `seq_num` is the position in a batch. Can be calculated as
    `message_pos % seq_mod`

    The window is used solely to send `window_size` packets not waiting for ACK
    from reciever. We send packets while we can (n_sent packets < window_size).
    Then, update the left_bound on diff between recieved Request and left_bound
        This allows to move left_bound even when intermediate ACKs were lost
        0 - lost, 1 - lost, 2 - recieved ACK -> left_bound += 3
    Don't update `m_pos` as it points at the not yet sent packet
    left bound     left bound
    v              v
    |0 1 2| 3 -> 0 |1 2 3|
       ^            ^
       message pos  message pos

    On each iteration check for packet from reciever. Got one? Move the `left_bound`
    Now we can send another packet (unless we sent all the packets and waiting for
    any of their ACKs). On each send update `m_pos`.
    If timed out, resend the message from `left_bound`.
    """

    s_to_r_stream: PacketQueue = attrs.field(repr=lambda v: f"{id(v._queue):x}"[~4:])
    r_to_s_stream: PacketQueue = attrs.field(repr=lambda v: f"{id(v._queue):x}"[~4:])
    message: Sequence[str] = [""]
    window_size: int = 10
    timeout: float = 1.0
    n_sent: int = attrs.field(init=False, default=0)
    num_packets: int = attrs.field(init=False, default=0)
    should_terminate: bool = attrs.field(init=False, default=False)
    # bool state whether the message has been sent or not
    done: bool = attrs.field(init=False, default=True)
    left_bound: int = attrs.field(init=False)
    m_pos: int = attrs.field(init=False)
    last_send_time: float = attrs.field(init=False)
    seq_num: int = attrs.field(init=False)

    def send_packet(self, high_packet: Packet):
        # if not self.done:
        #     breakpoint()
        assert self.done, (self, threading.get_ident())
        slog.info(f"Gotta send {high_packet = }")
        # will have 100 chars in packet's payload
        self.message = list(map("".join, batched(f"{high_packet}\x00", 100)))
        self.num_packets = len(self.message)
        orig_loss = self.s_to_r_stream.loss_probability
        self.s_to_r_stream.loss_probability = 0
        self.s_to_r_stream.send(LPacket(seq_num=NIT, payload="S"))
        self.s_to_r_stream.loss_probability = orig_loss
        self._update_stream_vars()
        slog.info(f"Updated stream vars")
        slog.info(f"{self.message = }, {self.num_packets = }")
        slog.info(
            f"{self.m_pos = }, {self.left_bound = }, {self.window_size = }, {self.num_packets = }"
        )
        self.done = False

    def send_termination_packet(self):
        slog.info(f"Terminating")
        orig_loss = self.s_to_r_stream.loss_probability
        self.s_to_r_stream.loss_probability = 0
        self.s_to_r_stream.send(LPacket(seq_num=EOT, payload="S"))
        self.s_to_r_stream.loss_probability = orig_loss
        self.done = True
        self.should_terminate = True

    def _update_stream_vars(self):
        self.left_bound = 0  # left boundary of the window
        self.m_pos = 0  # message position
        self.last_send_time = 0

    def run(self):
        seq_mod = self.window_size + 1
        self.seq_num = 0
        self._update_stream_vars()
        while not self.should_terminate:
            if self.r_to_s_stream:
                packet = self.r_to_s_stream.recieve()
                slog.debug(f"Recieved ACK for {packet.seq_num}")
                slog.debug(
                    f"Window before update {self.message[self.left_bound : self.left_bound + self.window_size]!r}"
                )
                # we can ignore "casting" left_bound to [0, seq_mod - 1],
                # because (a-b)%c = a%c - b%c (assuming, -b%c is within [0, seq_mod - 1])
                # (a-b%c)%c === (a-b)%c
                # calculate the difference between recieved seq_num and current left_bound
                # then move the left_bound so that it "points" at the next yet unACKed packet
                self.left_bound += (packet.seq_num - self.left_bound) % seq_mod
                slog.debug(
                    f"Window after update {self.message[self.left_bound : self.left_bound + self.window_size]!r}"
                )

            # if self.message[0]:
            #     slog.info(
            #         (
            #             f"{self.m_pos < min(self.left_bound + self.window_size, self.num_packets) = }\n{self.message}\n",
            #             f"{self.m_pos = }, {self.left_bound = }, {self.window_size = }, {self.num_packets = }",
            #         )
            #     )
            if self.m_pos < min(self.left_bound + self.window_size, self.num_packets):
                packet = LPacket(
                    seq_num=self.m_pos % seq_mod,
                    payload=self.message[self.m_pos],
                )
                slog.debug(f"Sent {packet}")
                self.s_to_r_stream.send(packet)
                self.m_pos += 1
                self.n_sent += 1
                # self.seq_num = (self.seq_num + 1) % seq_mod
                last_send_time = time.monotonic()

            if self.last_send_time + self.timeout < time.monotonic():
                # slog.debug(
                #     f"Timed out, resending from {self.left_bound} ({self.message[self.left_bound : self.left_bound + 1]})"
                # )
                self.m_pos = self.left_bound

            if self.m_pos >= self.num_packets:
                # self.message = [""]
                self.done = True

        slog.info(f"Sender terminated {self}")


@attrs.define
class Receiver:
    """Go-Back-N reciever

    Recieve packets and send ACKs for recieved ones.
    The sent ACK tells the sender which seq_num packet
    we're expecting, not the one we've successfully recieved
    """

    s_to_r_stream: PacketQueue = attrs.field(repr=lambda v: f"{id(v._queue):x}"[~4:])
    r_to_s_stream: PacketQueue = attrs.field(repr=lambda v: f"{id(v._queue):x}"[~4:])
    received_packets: Queue[Packet]
    window_size: int = 10
    n_recieved: int = attrs.field(init=False, default=0)
    received_payload: str = attrs.field(init=False, default="")
    terminated: bool = attrs.field(init=False, default=False)

    def run(self):
        seq_mod = self.window_size + 1
        expected_seq_num = 0

        n_right = 0
        n_wrong = 0

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()

            # transfer ended
            if packet.seq_num == EOT:
                rlog.info(f"I'm terminating {self}")
                self.terminated = True
                self.received_packets.put(Packet(Action.TERM, UID(""), ""))
                break

            if packet.seq_num == NIT:
                expected_seq_num = 0
                self.r_to_s_stream.send(
                    LPacket(seq_num=expected_seq_num, payload="NIT ACK")
                )

            self.n_recieved += 1
            if packet.seq_num == expected_seq_num:
                n_right += 1
                rlog.info(f"Recieved {packet}")
                self.received_payload += packet.payload
                if self.received_payload.endswith("\x00"):
                    rlog.info("HERE")
                    self.received_packets.put(
                        Packet.from_string(self.received_payload[:~0])
                    )
                    rlog.info(Packet.from_string(self.received_payload[:~0]))
                    self.received_payload = ""
                rlog.debug(
                    f"Sent ACK {expected_seq_num} Request {(expected_seq_num + 1) % seq_mod}"
                )
                expected_seq_num = (expected_seq_num + 1) % seq_mod
            else:
                n_wrong += 1
                rlog.debug(f"Ignoring {packet}: was expecting {expected_seq_num}")
                rlog.debug(f"Sending Request {expected_seq_num}")
            rlog.debug(f"Current message {self.received_payload!r}")
            self.r_to_s_stream.send(LPacket(seq_num=expected_seq_num, payload="ACK"))

        rlog.debug(f"Recieved message: {self.received_payload}")
        rlog.debug(f"{n_right = }")
        rlog.debug(f"{n_wrong = }")

        self.terminated = True
