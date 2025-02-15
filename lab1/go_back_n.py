from collections.abc import ByteString, Collection, Container, MutableSequence, Sequence
import attrs
import enum
import random
import queue
import time

# STOP_TRANSFER = -1
STOP_TRANSFER = "\x00"


class Status(enum.IntEnum):
    ACK = enum.auto()


@attrs.define
class Packet:
    seq_num: int
    payload: str
    # status: Status


@attrs.define
class PacketQueue:
    loss_probability: float
    latency: float = 0.1  # in sec
    _queue: queue.Queue[Packet] = attrs.field(init=False, factory=queue.Queue)

    def send(self, packet: Packet):
        # emulate network latency
        time.sleep(self.latency)
        if random.random() < self.loss_probability:
            print(f"{packet} was lost!")
            return
        self._queue.put(packet)

    def recieve(self):
        # if self._queue.empty():
        #     return None
        return self._queue.get()

    def __bool__(self) -> bool:
        return not self._queue.empty


@attrs.define
class Sender:
    """

    Say we have a message with len 10. Window size is 3.
    Split into batches: [1, 2, 3], [4, 5, 6], [7, 8, 9], [10]
    Then, the seq_nums will be [0, 1, 2], [0, 1, 2], [0, 1, 2], [0]
    So, `seq_num` is the position in a batch. Can be calculated as
    `message_pos % window_size`, where `message_pos = base_num + window_pos`

    The window is used solely to send `window_size` packets not waiting for ACK
    from reciever. We send `window_size` packets first. For each ACK'ed packet,
    move the window (`base_num += 1`) but don't update `window_pos`.
    left bound     left bound
    v              v
    |0 1 2| 3 -> 0 |1 2 3|
       ^              ^
       window pos     window pos

    On each send, update `window_pos`
    If `window_pos == window_size`, wait for packet from reciever.
    If timed out, resend the window from positions 0 to `window_pos`.
        We can time out not only when the `window_pos == window_size`
    """

    sender_to_reciever_ch: PacketQueue
    reciever_to_sender_ch: PacketQueue
    message: str
    base_num: int = 0  # left boundary of the window
    window_pos: int = 0  # current position in the window
    window_size: int = 10
    seq_num: int = 0  # pos in windowed batch
    sleep_time: float = 0.2
    timeout_time: float = 1.0
    num_packets: int = attrs.field(init=False)
    # _buffer: queue.Queue[str] = attrs.field(init=False, alias="_buffer")
    _buffer: Sequence[str] = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.message += STOP_TRANSFER
        self.num_packets = len(self.message)
        self._buffer = []
        # self._buffer = queue.Queue(self.window_size)

    def _send_window(self):
        self.seq_num = 0

        # while self.base_num + self.seq_num < min(self.base_num + self.window_size, len(message)):
        while self.seq_num < min(self.window_size, self.num_packets - self.base_num):
            self.send()
            self.seq_num += 1

        # m_pos = self.base_num
        # for m_pos in range(self.base_num, min(self.base_num + self.window_size, len(message))):
        #     self.sender_to_reciever_ch.send(Packet(seq_num=self.seq_num, payload=message[m_pos]))
        # self.seq_num = m_pos - self.base_num

        # curr_pos_in_message = self.base_num + self.seq_num
        # while curr_pos_in_message < len(message):
        #     self.sender_to_reciever_ch.send(Packet(seq_num=self.seq_num, payload=message[curr_pos_in_message]))
        #     self.seq_num += 1
        #     curr_pos_in_message += 1

    def run(self):
        while self.base_num < self.num_packets:
            if reciever_to_sender_ch:
                packet = self.reciever_to_sender_ch.recieve()
                self.base_num += 1
                self.window_pos -= 1

            if self.window_pos < self.window_size:
                m_pos = self.base_num + self.window_pos
                self.sender_to_reciever_ch.send(
                    Packet(
                        seq_num=m_pos % self.num_packets,
                        payload=self.message[m_pos],
                    )
                )

        # num_packets = len(message)
        # self._buffer = message[:self.window_size]
        # for chunk in message[:self.window_size]:
        #     self._buffer.append(chunk)

        while True:
            if reciever_to_sender_ch:
                self.recieve_and_send()

            if self.window_pos == self.window_size - 1:
                self.wait()
            else:
                self.send()

            # transmission complete
            if self.base_num >= self.num_packets:
                break

            time.sleep(self.sleep_time)

    def send(self):
        m_pos = self.base_num + self.window_pos
        if m_pos < self.num_packets:
            pkt = Packet(seq_num=m_pos % self.window_size, payload=self.message[m_pos])
        else:
            pkt = Packet(seq_num=STOP_TRANSFER, payload="")
        print(f"Sent {pkt}")
        self.sender_to_reciever_ch.send(pkt)

    def recieve_and_send(self):
        pkt = reciever_to_sender_ch.recieve()
        print(f"Recieved ACK for {pkt}")
        self.base_num += 1
        # self.window_pos += 1
        # self.seq_num = (self.seq_num + 1) % self.window_size
        self.send()

    def wait(self):
        start_time = time.time()

        while not reciever_to_sender_ch:
            time.sleep(self.sleep_time)
            if time.time() - start_time > self.timeout_time:
                print("Timed out waiting for reciever's ACK. Resending window")
                self._send_window()


@attrs.define
class Reciever:
    sender_to_reciever_ch: PacketQueue
    reciever_to_sender_ch: PacketQueue
    window_size: int = 10
    expected_seq_num: int = 0

    def run(self):
        while True:
            # will block until there is something to recieve
            packet = sender_to_reciever_ch.recieve()
            if packet.seq_num == self.expected_seq_num:
                print(f"Recieved {packet}")
                reciever_to_sender_ch.send(
                    Packet(seq_num=self.expected_seq_num, payload="")
                )
                print(f"Sent ACK for {self.expected_seq_num} batch packet")
                self.expected_seq_num = (self.expected_seq_num + 1) % self.window_size
            else:
                print(f"Ignoring {packet}: was expecting {self.expected_seq_num}")

            # transfer ended
            if packet.payload == STOP_TRANSFER:
                break


if __name__ == "__main__":
    loss_probability = 0.3
    # a channel from sender to reciever: sender puts, reciever gets
    sender_to_reciever_ch = PacketQueue(loss_probability=loss_probability)
    # a channel from reciever to sender: reciever puts, sender gets
    reciever_to_sender_ch = PacketQueue(loss_probability=loss_probability)
