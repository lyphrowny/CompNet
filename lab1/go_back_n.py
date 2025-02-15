import warnings
import attrs
import random
import queue
import time
from threading import Thread

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
                if packet.seq_num != (
                    expected_seq_num := left_bound % self.window_size
                ):
                    warnings.warn(
                        (
                            f"Recieved ACK {packet.seq_num} doesn't match "
                            f"the expected seq_num {expected_seq_num}"
                        ),
                        UserWarning,
                    )
                    m_pos = left_bound
                else:
                    left_bound += 1

            if m_pos < min(left_bound + self.window_size, self.num_packets):
                packet = Packet(
                    seq_num=m_pos % self.window_size,
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

        self.handle_eot()

    def handle_eot(self):
        eot_packet = Packet(seq_num=EOT, payload="S")
        # send eot_packets until EOT ACK is recieved
        while True:
            slog.debug("Sending EOT")
            self.sender_to_reciever_ch.send(eot_packet)

            if self.reciever_to_sender_ch:
                slog.debug("Got EOT ACK")
                packet = self.reciever_to_sender_ch.recieve()
                if packet.seq_num == EOT:
                    break
            time.sleep(self.timeout)


@attrs.define
class Reciever:
    """Go-Back-N reciever

    Recieve packets and send ACKs for recieved ones
    """

    sender_to_reciever_ch: PacketQueue
    reciever_to_sender_ch: PacketQueue
    window_size: int = 10
    n_recieved: int = 0
    eot_disconnect_timeout: float = 1.0

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
                rlog.debug(f"Sent ACK for {expected_seq_num}")
                self.reciever_to_sender_ch.send(
                    Packet(seq_num=expected_seq_num, payload="")
                )
                expected_seq_num = (expected_seq_num + 1) % self.window_size
            else:
                n_wrong += 1
                rlog.debug(f"Ignoring {packet}: was expecting {expected_seq_num}")
                rlog.debug(
                    f"Sending prev ACK {(expected_seq_num - 1) % self.window_size}"
                )
                self.reciever_to_sender_ch.send(
                    Packet(
                        seq_num=(expected_seq_num - 1) % self.window_size,
                        payload="",
                    )
                )

        self.handle_eot()

        rlog.debug(f"Recieved message: {message}")
        rlog.debug(f"{n_right = }")
        rlog.debug(f"{n_wrong = }")

    def handle_eot(self):
        eot_packet = Packet(seq_num=EOT, payload="R")
        resend = True
        last_send_time = 0
        while True:
            if resend:
                rlog.debug("Sending EOT ACK")
                self.reciever_to_sender_ch.send(eot_packet)
                resend = False
                last_send_time = time.monotonic()

            # we recieve? we send back!
            # this means, the sender didn't get our EOT ACK
            if self.sender_to_reciever_ch:
                packet = self.sender_to_reciever_ch.recieve()
                rlog.debug("Got EOT again")
                if packet.seq_num == EOT:
                    resend = True
                else:
                    rlog.debug(f"Expected EOT, got {packet.seq_num}")

            # probably, the sender got the ACK for the EOT packet
            if time.monotonic() - last_send_time > self.eot_disconnect_timeout:
                rlog.debug("EOT timeout")
                break


if __name__ == "__main__":
    latency = 0.05
    loss_probability = 0.2
    window_size = 3
    # a channel from sender to reciever: sender puts, reciever gets
    sender_to_reciever_ch = PacketQueue(
        loss_probability=loss_probability,
        latency=latency,
    )
    # a channel from reciever to sender: reciever puts, sender gets
    reciever_to_sender_ch = PacketQueue(
        loss_probability=0.1,
        latency=latency,
    )

    from string import ascii_lowercase

    msg = ascii_lowercase[:5]
    sender = Sender(
        sender_to_reciever_ch,
        reciever_to_sender_ch,
        message=msg,
        window_size=window_size,
    )
    reciever = Reciever(
        sender_to_reciever_ch,
        reciever_to_sender_ch,
        window_size=window_size,
    )

    # threads = [Thread(target=sender.run), Thread(target=reciever.run)]

    # for th in threads:
    #     th.start()
    # for th in threads:
    #     th.join()

    th_s = Thread(target=sender.run)
    th_r = Thread(target=reciever.run)

    th_s.start()
    th_r.start()

    th_s.join()
    th_r.join()

    print(f"{sender.n_sent = }")
    print(f"{reciever.n_recieved = }")
