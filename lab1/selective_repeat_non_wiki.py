import logging
import time
from collections import deque
from collections.abc import MutableSequence

import attrs

from .stream import EOT, Packet, PacketQueue
from .utils import get_logger


@attrs.define
class Sender:
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    message: str
    window_size: int = 10
    timeout: float = 1.0
    n_sent: int = attrs.field(init=False, default=0)
    log: logging.Logger = attrs.field(init=False, default=get_logger("s"))

    def run(self):
        left_bound = 0
        m_pos = 0
        seq_mod = self.window_size * 2
        _buffer: deque[Packet] = deque()
        num_packets = len(self.message)

        def fill_buffer():
            # don't know why others are file without nonlocal
            # but m_pos not
            nonlocal m_pos

            # fill buffer up-to-capacity or add what's left to transmit
            for _ in range(min(self.window_size - len(_buffer), num_packets - m_pos)):
                _buffer.append(
                    Packet(
                        seq_num=m_pos % seq_mod,
                        payload=self.message[m_pos],
                        # hack: this way all newly created messages
                        # will be sent as if their timeout was exhausted
                        sent_at=-time.monotonic(),
                    )
                )
                m_pos += 1

            # # shrink buffer so that it's either `window_size` or what's left to get ACK from
            # while len(_buffer) > min(self.window_size, num_packets - left_bound):
            #     _buffer.popleft()

        # init buffer
        fill_buffer()
        self.log.debug(f"Filled buffer {_buffer}")

        while left_bound < num_packets:
            if self.r_to_s_stream:
                packet = self.r_to_s_stream.recieve()
                self.log.debug(f"Recieved ACK {packet.seq_num}")
                diff = (packet.seq_num - left_bound) % seq_mod

                # the ACK for base or any other packet was lost,
                # but the reciever has the full window and now expects
                # a new one
                if diff == self.window_size:
                    # mark all the packets as ACKed
                    for packet in _buffer:
                        packet.sent_at = 2 * time.monotonic()
                elif diff > self.window_size:
                    self.log.error(f"{diff} > {self.window_size} - shouldn't happen")
                else:
                    if len(_buffer) > diff:
                        # mark as ACKed
                        _buffer[diff].sent_at = 2 * time.monotonic()

                # remove consequtive ACKed packets
                while _buffer and _buffer[0].sent_at > time.monotonic():
                    _buffer.popleft()
                    left_bound += 1

                # add new packets
                fill_buffer()
                self.log.debug(f"Refilled buffer {_buffer}")

            for packet in _buffer:
                # retransmit lost packets or transmit new packets for the 1st time
                if packet.sent_at + self.timeout < time.monotonic():
                    self.n_sent += 1
                    packet.sent_at = time.monotonic()
                    self.log.debug(f"Sent packet {packet}")
                    self.s_to_r_stream.send(packet)

            # if self.r_to_s_stream:
            #     packet = self.r_to_s_stream.recieve()
            #     self.log.debug(f"Recieved ACK Request {packet.seq_num}")
            #     diff = (packet.seq_num - left_bound) % seq_mod
            #     self.log.debug(f"left_bound before {left_bound}, {diff = }")
            #     left_bound += diff
            #     self.log.debug(f"left_bound after {left_bound}")
            #     # add new packets, remove ACKed packets
            #     fill_buffer(diff)
            #     self.log.debug(f"Refilled buffer {_buffer}")

            # for packet in _buffer:
            #     # retransmit lost packets or transmit new packets for the 1st time
            #     if packet.sent_at + self.timeout < time.monotonic():
            #         self.n_sent += 1
            #         packet.sent_at = time.monotonic()
            #         self.log.debug(f"Sent packet {packet}")
            #         self.s_to_r_stream.send(packet)


@attrs.define
class Reciever:
    s_to_r_stream: PacketQueue
    r_to_s_stream: PacketQueue
    window_size: int = 10
    n_recieved: int = attrs.field(init=False, default=0)
    log: logging.Logger = attrs.field(init=False, default=get_logger("r"))

    def run(self):
        seq_mod = self.window_size * 2
        b_mod = self.window_size
        _buffer: MutableSequence[Packet | None] = [None] * b_mod
        message = ""
        expected_seq_num = 0

        while True:
            # will block until there is something to recieve
            packet = self.s_to_r_stream.recieve()
            self.log.debug(f"Recieved packet {packet}")

            if packet.seq_num == EOT:
                break

            self.n_recieved += 1
            # if packet num is within the window
            if (packet.seq_num - expected_seq_num) % seq_mod < b_mod:
                self.log.debug(f"Inserting new packet, before {_buffer}")
                _buffer[packet.seq_num % b_mod] = packet
                self.log.debug(f"Inserted new packet, after {_buffer}")

                self.log.debug(f"Sent ACK {packet.seq_num}")
                self.r_to_s_stream.send(Packet(seq_num=packet.seq_num, payload="ACK"))

                self.log.debug(f"Buffer before removal {_buffer}")
                while _buffer[expected_seq_num % b_mod] is not None:
                    message += _buffer[expected_seq_num % b_mod].payload
                    _buffer[expected_seq_num % b_mod] = None
                    expected_seq_num = (expected_seq_num + 1) % seq_mod
                self.log.debug(f"Buffer after removal {_buffer}")

            else:
                self.log.debug("Out of window packet, ignoring")
                self.log.debug(f"Sent ACK Request {expected_seq_num}")
                self.r_to_s_stream.send(
                    Packet(
                        seq_num=expected_seq_num,
                        payload="REQUEST",
                    )
                )

            # if packet.seq_num == expected_seq_num:
            #     message += packet.payload
            #     expected_seq_num = (expected_seq_num + 1) % seq_mod

            #     self.log.debug(f"Buffer before removal {_buffer}")
            #     while _buffer[expected_seq_num % b_mod] is not None:
            #         message += _buffer[expected_seq_num % b_mod].payload
            #         _buffer[expected_seq_num % b_mod] = None
            #         expected_seq_num = (expected_seq_num + 1) % seq_mod
            #     self.log.debug(f"Buffer after removal {_buffer}")
            # # if packet num is within the window
            # elif (packet.seq_num - expected_seq_num) % seq_mod < b_mod:
            #     self.log.debug(f"Inserting new packet, before {_buffer}")
            #     _buffer[packet.seq_num % b_mod] = packet
            #     self.log.debug(f"Inserted new packet, after {_buffer}")
            # else:
            #     self.log.debug("Out of window packet, ignoring")

            # self.log.debug(f"Sent ACK Request {expected_seq_num}")
            # self.r_to_s_stream.send(
            #     Packet(
            #         seq_num=expected_seq_num,
            #         payload="REQUEST",
            #     )
            # )

        self.log.debug(f"{message = }")
