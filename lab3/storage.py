from collections import defaultdict
import copy
import difflib
import enum
from collections.abc import (
    Collection,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
)
from functools import wraps
from itertools import batched, pairwise, starmap
import logging
import math
from pathlib import Path
from queue import Queue
import random
from threading import Thread
import time
from typing import ClassVar, Literal, NewType, Self, Sequence, cast, override

import attrs

from .network import Network, NodeProto, Paths, Point
from .stream import PacketQueue as Stream
from .go_back_n import Receiver, Sender
from .high_transfer import UID, Action, Packet

# EOT = -1

Addr = NewType("Addr", str)
# UID = NewType("UID", str)
SStream = NewType("SStream", Stream)
RStream = NewType("RStream", Stream)


# end of stored data
EOS = "\x00"


@attrs.define
class AddressMapper:
    """A global addr-to-stream hub."""

    address_to_stream: ClassVar[MutableMapping[Addr, Stream]] = {}

    @staticmethod
    def _create_ptp_addr(
        t_uid: UID,
        p_uid: UID,
        stream_type: Literal["s", "r"],
    ) -> Addr:
        return cast(Addr, f"{t_uid}-{p_uid}_{stream_type}")

    @staticmethod
    def _create_ptp_addrs(
        t_uid: UID,
        p_uid: UID,
    ) -> tuple[Addr, Addr]:
        return (
            AddressMapper._create_ptp_addr(t_uid, p_uid, "s"),
            AddressMapper._create_ptp_addr(t_uid, p_uid, "r"),
        )

    @classmethod
    def add_streams(cls, peer1_uid: UID, peer2_uid: UID) -> None:
        r"""Add send and receive streams for peers.

        2 streams will be created: a SendStream from peer1 to peer2
        and a ReceiveStream from peer2 to peer1, thus forming a one-way connection.

        For a--b to have a duplex connection, one has to form 2 one-way connections
        = call this function 2 times: (a, b) and (b, a). This is unfortunate, but
        the Receiver and Sender were implemented as separate instances, which listen
        to different channels. If Receiver and Sender were merged, they could use
        only one stream.
        """
        s_to_r_addr, r_to_s_addr = AddressMapper._create_ptp_addrs(peer1_uid, peer2_uid)
        cls.address_to_stream[s_to_r_addr] = Stream()
        cls.address_to_stream[r_to_s_addr] = Stream()

    @classmethod
    def get(cls, t_uid: UID, p_uid: UID) -> tuple[SStream, RStream]:
        """Return streams for one-way connection from transmitter to peer."""
        ttp, ptt = AddressMapper._create_ptp_addrs(t_uid, p_uid)
        return cls.address_to_stream[ttp], cls.address_to_stream[ptt]


@attrs.define
class Peer:
    uid: UID
    # a receiver stream which the peer listens on
    # ttp stands for transmitter-to-peer
    # send stream, because transmitter is the main role,
    # so it sends to peer
    real_ttp_stream: SStream
    dummy_ttp_stream: SStream
    real_ptt_stream: RStream
    dummy_ptt_stream: RStream
    real_sender: Sender = attrs.field(repr=lambda v: f"\n\n{v}\n\n")
    dummy_receiver: Receiver = attrs.field(repr=lambda v: f"\n\n{v}\n\n")
    dummy_sender: Sender = attrs.field(repr=lambda v: f"\n\n{v}\n\n")
    real_receiver: Receiver = attrs.field(repr=lambda v: f"\n\n{v}\n\n")
    ths: Iterable[Thread] = attrs.field(init=False)

    @classmethod
    def from_payload(cls, t_uid: UID, payload: str, received_packets: Queue[Packet]):
        p_uid = cast(UID, payload[:4])
        real_ttp_stream, dummy_ptt_stream = AddressMapper.get(t_uid, p_uid)
        dummy_ttp_stream, real_ptt_stream = AddressMapper.get(p_uid, t_uid)
        # sends real packets from T to P, receives ACKS, therefore dummy receiver
        real_sender = Sender(real_ttp_stream, dummy_ptt_stream)
        dummy_receiver = Receiver(real_ttp_stream, dummy_ptt_stream, Queue())

        # sends ACKS, receiver real packets from P to T, therefore dummy sender
        dummy_sender = Sender(dummy_ttp_stream, real_ptt_stream)
        real_receiver = Receiver(dummy_ttp_stream, real_ptt_stream, received_packets)
        return cls(
            p_uid,
            real_ttp_stream=real_ttp_stream,
            dummy_ttp_stream=dummy_ttp_stream,
            real_ptt_stream=real_ptt_stream,
            dummy_ptt_stream=dummy_ptt_stream,
            real_sender=real_sender,
            dummy_receiver=dummy_receiver,
            dummy_sender=dummy_sender,
            real_receiver=real_receiver,
        )

    def start(self):
        self.ths = [
            Thread(target=self.real_sender.run),
            # Thread(target=self.dummy_receiver.run),
            # Thread(target=self.dummy_sender.run),
            Thread(target=self.real_receiver.run),
        ]
        for th in self.ths:
            th.start()

    def terminate(self):
        while not self.real_sender.to_send_queue.empty():
            time.sleep(0.1)
        self.real_sender.send_termination_packet()
        # self.dummy_sender.send_termination_packet()
        for th in self.ths:
            th.join()

    def send_packet(self, packet: Packet):
        self.real_sender.send_packet(packet)

    # def get_packet(self) -> Packet | None:
    #     if not self.received_packets.empty():
    #         return self.received_packets.get()
    #     return None


def if_for_me(func):
    # @wraps
    def wrapper(self: "Transmitter", packet: Packet, *a, **kw):
        if packet.receiver_uid == UID("") or packet.receiver_uid == self.uid:
            func(self, packet, *a, **kw)
        else:
            # packet = attrs.evolve(packet, from_uid=self.uid)
            self._relay(packet)

    return wrapper


@attrs.frozen(order=True)
class TransmitterNode(NodeProto):
    uid: UID
    pt: Point

    def __repr__(self):
        return f"{self.uid}"

    @staticmethod
    def dist(a: "TransmitterNode", b: "TransmitterNode") -> float:
        return math.hypot(*attrs.astuple(a.pt - b.pt))


@attrs.define
class Storage:
    batch_size: int = attrs.field(default=14)
    stored_data: str = attrs.field(init=False, default="")
    _stored_iterator: Iterator[str] = attrs.field(init=False, default=None)
    is_ready: bool = attrs.field(init=False, default=False)

    def store(self, data: str) -> None:
        """Store data until it has EOS, then init iterator."""
        self.stored_data += data
        if self.stored_data.endswith(EOS):
            self.stored_data = self.stored_data[:~0]
            self._stored_iterator = batched(self.stored_data, self.batch_size)
            self.is_ready = True
        print(f"{self.stored_data = !r}")
        print(f"{self.is_ready = }")
        # self._stored_iterator = batched("ABC\x00", self._batch_size)

    def give(self) -> str:
        try:
            return "".join(next(self._stored_iterator))
        except StopIteration:
            self.is_ready = False
            return EOS


@attrs.define
class DesignatedStorage:
    batch_size: int = attrs.field(default=14)
    stored_data: MutableMapping[int, str] = attrs.field(
        init=False,
        default=defaultdict(str),
    )

    def store(self, data: str) -> None:
        """Store data until it has EOS, then init iterator."""
        data_id = int(data[:4])
        data = data[4:]
        self.stored_data[data_id] = data
        print(f"{self.stored_data = }")
        # self._stored_iterator = batched("ABC\x00", self._batch_size)

    def assemble(self) -> str:
        return "".join(d for _, d in sorted(self.stored_data.items()))


@attrs.define
class Transmitter:
    uid: UID
    storage: Storage = attrs.field(factory=Storage)
    peers: MutableMapping[UID, Peer] = attrs.field(init=False, factory=dict)
    _should_terminate: bool = attrs.field(init=False, default=False)
    _packet_queue: Queue[Packet] = attrs.field(init=False, factory=Queue)

    def run(self):
        while not self._should_terminate:
            if not self._packet_queue.empty():
                # breakpoint()
                self._receive_and_act()
        # while not self._packet_queue.empty():
        #     self._receive_and_act()

    def _receive_and_act(self):
        packet = self._packet_queue.get()
        match packet.action:
            case Action.STORE:
                self._store(packet)
            case Action.GIVE:
                self._give(packet)
            case Action.ADD:
                self._add_peer(packet)
            case Action.TERM:
                self._terminate(packet)

    # def get_packet(self):
    #     for peer in self.peers.values():
    #         if peer.

    # def has_incoming_packets(self):
    #     for peer in self.peers.values():
    #         if peer.
    # def receive(self):
    #     while self.has_unread_packets():
    #         packet: Packet = self.read_packet()

    #         match packet.action:
    #             case Action.STORE:
    #                 self._store(packet)
    #             case Action.GIVE:
    #                 self._give(packet)
    #             case Action.ADD:
    #                 self._add_peer(packet)
    # case Action.RELAY:
    #     self._relay(packet)

    @if_for_me
    def _store(self, packet: Packet):
        print(f"Storing {packet = }")
        self.storage.store(packet.payload)
        # if packet.receiver_uid == self.uid:
        #     self.storage.store(packet.payload)
        # else:
        #     self.retransmit(packet)

    def _relay(self, packet: Packet):
        peer_uid = packet.receiver_uid
        peer = self.peers.get(peer_uid, None)
        got_from = packet.from_uid
        packet = attrs.evolve(packet, from_uid=self.uid)
        if peer is None:
            self._send_to_all_but_one(got_from, packet)
        else:
            self.send(peer_uid, packet)

    def _send_to_all_but_one(self, exclude_peer_uid: UID, packet: Packet):
        print(
            # f"Gotta send to all but {self.peers.keys() - exclude_peer_uid = } from {self.uid}"
            f"Gotta send to {self.peers.keys() - exclude_peer_uid} from {self.uid}"
        )
        for peer_uid in self.peers.keys() - exclude_peer_uid:
            print(f"Sending (relaying) to {peer_uid = } form {self.uid}")
            self.send(peer_uid, packet)

    def _give(self, packet: Packet):
        if packet.receiver_uid == self.uid:
            if not self.storage.is_ready:
                self._packet_queue.put(packet)
                return
            sender_uid = cast(UID, packet.payload[~3:])
            self._give_all(sender_uid)
            # print(f"{self.uid!r} for me, sending back to {sender_uid!r}")
            # packet = Packet(
            #     Action.STORE,
            #     sender_uid,
            #     self.storage.give(),
            # )
            # self.send(sender_uid, packet)
        else:
            print(f"{self.uid!r} not for me, relaying")
            packet = attrs.evolve(packet, from_uid=self.uid)
            # packet = attrs.evolve(packet, payload=f"{self.uid}{packet.payload}")
            self._relay(packet)

    def _give_all(self, send_to_uid: UID):
        print(f"{self.uid!r} for me, sending back to {send_to_uid!r}")
        while (data := self.storage.give()) != EOS:
            packet = Packet(
                Action.STORE,
                receiver_uid=send_to_uid,
                from_uid=self.uid,
                payload=data,
            )
            self._relay(packet)
            # self.send(send_to_uid, packet)
            # time.sleep(1)

    @if_for_me
    def _add_peer(self, packet: Packet):
        # if packet.receiver_uid == self.uid:
        print(f"{packet}")
        peer = Peer.from_payload(self.uid, packet.payload, self._packet_queue)
        peer.start()
        self.peers[peer.uid] = peer
        # else:
        #     self.retransmit(packet)

    @if_for_me
    def _terminate(self, packet: Packet):
        print(f"Got here")
        for peer in self.peers.values():
            peer.terminate()

        self._should_terminate = True
        print(f"peers terminated")

        # close all the threads for sender and receiver

    # def send(self, peer_uid: UID, action, receiver_uid, payload):
    #     peer = self.peers[peer_uid]
    #     _send(peer.stream, action, receiver_uid, payload)

    def send(self, peer_uid: UID, packet: Packet):
        peer = self.peers[peer_uid]
        peer.send_packet(packet)


# @attrs.define
# class StorageNode(NodeProto):
#     idx: int
#     latency: float
#     storage: str = attrs.field(init=False)
#     _storage_iterator: Iterator[str] = attrs.field(init=False, default=None)

#     @override
#     def __hash__(self) -> int:
#         return hash(self.idx)

#     @override
#     def __eq__(self, other: object) -> bool:
#         if not isinstance(other, "StorageNode"):
#             raise NotImplementedError
#         return self.idx == other.idx

#     @override
#     def __lt__(self, other: object) -> bool:
#         if not isinstance(other, "StorageNode"):
#             raise NotImplementedError
#         return self.idx < other.idx

#     @override
#     @staticmethod
#     def dist(a: object, b: object) -> float:
#         if not isinstance(a, "StorageNode"):
#             raise NotImplementedError
#         if not isinstance(b, "StorageNode"):
#             raise NotImplementedError
#         return a.latency + b.latency

#     def next_chunk(self):
#         if self._storage_iterator is None:
#             self._storage_iterator = iter(self.storage)
#         try:
#             return next(self._storage_iterator)
#         except StopIteration:
#             return EOT


def _unset_debug_logging_level():
    for logger in logging.getLogger().getChildren():
        # logger.setLevel(logging.INFO)
        logger.setLevel(logging.ERROR)


def _add_connection(peer1_uid: UID, peer2_uid: UID):
    AddressMapper.add_streams(peer1_uid, peer2_uid)
    AddressMapper.add_streams(peer2_uid, peer1_uid)


def split_data(data: str, uids: Sequence[UID], chunk_len: int):
    data_per_uid = defaultdict(list)
    for i, chunk in enumerate(batched(data, chunk_len)):
        uid_to_store_id = random.choice(uids)
        data_per_uid[uid_to_store_id].append(f"{i:04}{''.join(chunk)}")
    return data_per_uid


def make_uid():
    return cast(UID, f"{uuid.uuid4()}".split("-")[1])


def make_uids(num_uids: int):
    return [make_uid() for _ in range(num_uids)]


def make_tnodes(
    uids: Iterable[UID],
    points: Iterable[Point],
) -> Mapping[UID, TransmitterNode]:
    tnodes = starmap(TransmitterNode, zip(uids, points))
    return dict(zip(uids, tnodes))


def get_unique_pairs(shortest_paths: Paths):
    return {
        (peer1, peer2)
        for path in shortest_paths.values()
        for peer1, peer2 in pairwise(path)
    }


def make_transmiters(
    shortest_paths: Paths,
    conductor_uid: UID,
    batch_size: int = 14,
) -> Mapping[UID, Transmitter]:
    transmitters = {
        conductor_uid: Transmitter(
            conductor_uid,
            DesignatedStorage(batch_size=batch_size),
        )
    }
    already_connected: set[tuple[NodeProto, NodeProto]] = set()
    for tnode, path in shortest_paths.items():
        print(f"{tnode = }, {path = }")
        transmitters[tnode.uid] = Transmitter(tnode.uid, Storage(batch_size=batch_size))
        for peer1, peer2 in pairwise(path):
            if (peer1, peer2) in already_connected:
                continue
            already_connected.add((peer1, peer2))
            print((peer1, peer2))
            _add_connection(peer1.uid, peer2.uid)

    print(f"{transmitters.keys() = }")
    return transmitters


def befriend_peers(
    unique_peer_pairs: set[tuple[NodeProto, NodeProto]],
    transmitters: Mapping[UID, Transmitter],
):
    # def befriend_peers(shortest_paths: Paths, conductor: Transmitter):
    for peer1, peer2 in unique_peer_pairs:
        pkt1 = Packet(
            Action.ADD,
            receiver_uid=peer2.uid,
            from_uid=peer1.uid,
            # can't remove payload here, because ``from_uid`` might change,
            # thus making the wrong connection
            payload=f"{peer1.uid}",
        )
        pkt2 = Packet(
            Action.ADD,
            receiver_uid=peer1.uid,
            from_uid=peer2.uid,
            payload=f"{peer2.uid}",
        )
        # hack: originally, transmitter doesn't have anything "listening" on
        # receiver ports, so we can't just ``conductor._packet_queue.put(pkt)``
        transmitters[peer1.uid]._add_peer(pkt2)
        transmitters[peer2.uid]._add_peer(pkt1)
        # conductor.send(paths[1].uid, pkt2)


def make_data_distribution(
    data: str,
    n_consumers: int,
    batch_size: int,
) -> Iterable[Iterable[str]]:
    consumers = range(n_consumers)
    distributed_data: Iterable[Iterable[str]] = [[] for _ in consumers]
    for i, batch in enumerate(batched(data, batch_size)):
        distributed_data[random.choice(consumers)].append(f"{i:04}{''.join(batch)}")
    return distributed_data


def make_data_distribution(
    data: str,
    uids: Sequence[UID],
    batch_size: int,
) -> MutableMapping[UID, list[str]]:
    distributed_data: MutableMapping[UID, list[str]] = defaultdict(list)
    for i, batch in enumerate(batched(data, batch_size)):
        uid = random.choice(uids)
        distributed_data[uid].append(f"{i:04}{''.join(batch)}")
    return distributed_data


def distribute_data(
    distributed_data: MutableMapping[UID, list[str]],
    conductor: Transmitter,
) -> None:
    for uid, datas_to_store in distributed_data.items():
        for data_to_store in datas_to_store:
            conductor._packet_queue.put(
                Packet(
                    Action.STORE,
                    receiver_uid=uid,
                    from_uid=conductor.uid,
                    payload=data_to_store,
                )
            )
        # send the end of store marker
        conductor._packet_queue.put(
            Packet(
                Action.STORE,
                receiver_uid=uid,
                from_uid=conductor.uid,
                payload=EOS,
            ),
        )


def assemble_data(conductor: Transmitter, uids: Iterable[UID]):
    for uid in uids:
        conductor._packet_queue.put(
            Packet(
                Action.GIVE,
                receiver_uid=uid,
                from_uid=conductor.uid,
                payload=f"{conductor.uid}",
            )
        )


def wait_for_assemble_completion(conductor_storage: DesignatedStorage, n_batches: int):
    # hack: if called terminate_all without waiting,
    # the data won't be fully assembled. Can't think of a fix
    # for now
    while len(conductor_storage.stored_data) != n_batches:
        print(f"{len(conductor_storage.stored_data) = }, {n_batches = }")
        time.sleep(0.1)


def terminate_all(conductor: Transmitter, shortest_paths: Paths):
    # hack: to not remove again
    already_termed: set[UID] = set()
    for path in shortest_paths.values():
        # skip the 0th node, which is conductor
        for peer in reversed(path[1:]):
            if peer.uid in already_termed:
                continue
            already_termed.add(peer.uid)
            conductor._packet_queue.put(
                Packet(
                    Action.TERM,
                    receiver_uid=peer.uid,
                    from_uid=conductor.uid,
                    payload="",
                )
            )
    # terminate ourselves
    conductor._packet_queue.put(
        Packet(
            Action.TERM,
            receiver_uid=conductor.uid,
            from_uid=conductor.uid,
            payload="",
        )
    )


def linear(n_pts: int = 5) -> MutableSequence[Point]:
    return [Point(i, i) for i in range(n_pts)]


def circular(
    n_pts: int = 10,
    r: float = 3,
) -> MutableSequence[Point]:
    return [
        Point(
            math.cos(2 * math.pi * i / n_pts) * r,
            math.sin(2 * math.pi * i / n_pts) * r,
        )
        for i in range(n_pts)
    ]


def starlike(r: float = 3) -> MutableSequence[Point]:
    return [Point(0, 0), *circular(n_pts=5, r=r)]


if __name__ == "__main__":
    import uuid

    data = (Path(__file__).parent / "data.txt").read_text()
    from string import printable, ascii_letters, digits

    data = data

    _unset_debug_logging_level()

    simulation_type: Literal["c", "l1", "l2", "s"] = "c"
    batch_size = 10

    match simulation_type:
        case "c":
            num_nodes = 4
            # conductor idx in uids
            c_idx = 0

            uids = make_uids(num_uids=num_nodes)
            tnodes = make_tnodes(uids, circular(num_nodes))
            for uid, tnode in tnodes.items():
                print(f"{tnode.uid = }, {tnode.pt = }")
            net = Network.from_nodes(list(tnodes.values()), max_distance=4.3)
        case "l1":
            num_nodes = 3
            # conductor idx in uids
            c_idx = 0

            uids = make_uids(num_uids=num_nodes)
            tnodes = make_tnodes(uids, linear(num_nodes))
            for uid, tnode in tnodes.items():
                print(f"{tnode.uid = }, {tnode.pt = }")
            net = Network.from_nodes(list(tnodes.values()), max_distance=1.5)
        case "l2":
            num_nodes = 3
            # conductor idx in uids
            c_idx = 1

            uids = make_uids(num_uids=num_nodes)
            # latencies = [0.5, 0.3, 0.5]
            # latencies = [0.1, 0.1, 0.1]
            tnodes = make_tnodes(uids, linear(num_nodes))
            for uid, tnode in tnodes.items():
                print(f"{tnode.uid = }, {tnode.pt = }")
            net = Network.from_nodes(list(tnodes.values()), max_distance=1.5)
        case "s":
            num_nodes = 4
            # conductor idx in uids
            c_idx = 0

            uids = make_uids(num_uids=num_nodes - 1)
            # latencies = [0.5, 0.3, 0.5]
            # latencies = [0.1, 0.1, 0.1]
            tnodes = make_tnodes(uids, circular(num_nodes - 1))
            uids = [make_uid(), *uids]
            print(tnodes)
            tnodes[uids[0]] = TransmitterNode(uids[0], pt=Point(0, 0))
            print(tnodes)
            for uid, tnode in tnodes.items():
                print(f"{tnode.uid = }, {tnode.pt = }")
            net = Network.from_nodes(list(tnodes.values()), max_distance=3.1)
    print(net)
    # quit()
    # conductor
    c_uid = uids[c_idx]
    # print(f"{c_uid = }")
    del uids[c_idx]
    shortest_paths = net.ospf(tnodes[c_uid])
    print(shortest_paths)
    print(shortest_paths[tnodes[c_uid]])
    del shortest_paths[tnodes[c_uid]]
    unique_pairs = get_unique_pairs(shortest_paths)
    print(net)
    print(shortest_paths)
    # quit()

    distributed_data = make_data_distribution(data, uids, batch_size)

    transmitters = make_transmiters(
        shortest_paths,
        conductor_uid=c_uid,
        # +4 because we add batch id in form of "{id:04}"
        # to later reconstuct the original message
        batch_size=batch_size + 4,
    )
    conductor = transmitters[c_uid]
    print(transmitters)
    # quit()

    start_time = time.monotonic()
    ths = [Thread(target=t.run) for t in transmitters.values()]
    for th in ths:
        th.start()

    befriend_peers(
        unique_peer_pairs=unique_pairs,
        transmitters=transmitters,
    )
    print(f"{c_uid = }")
    for tmitter in transmitters.values():
        print(tmitter.uid, tmitter.peers.keys())
    distributed_data = make_data_distribution(
        data,
        uids,
        batch_size=batch_size,
    )
    distribute_data(distributed_data, conductor)
    # while True:
    #     time.sleep(5)
    # while True:
    #     print()
    #     print(f"{transmitters[uids[~1]].storage.stored_data!r}")
    #     print()
    #     print(f"{transmitters[uids[~0]].storage.stored_data!r}")
    #     print()
    #     print()

    #     time.sleep(5)
    assemble_data(conductor=conductor, uids=uids)
    wait_for_assemble_completion(
        conductor_storage=conductor.storage,
        n_batches=sum(
            map(len, distributed_data.values()),
        ),
    )
    terminate_all(
        conductor=conductor,
        shortest_paths=shortest_paths,
    )
    for th in ths:
        th.join()

    assembled_data = conductor.storage.assemble()
    assert assembled_data == data, "".join(difflib.unified_diff(assembled_data, data))
    print(f"{assembled_data = }")
    print()
    print(
        f"Time taken: {time.monotonic() - start_time:.3f} secs for {simulation_type = !r}"
    )
    quit()

    p_uid, tp_uid = uids

    t = Transmitter(c_uid, DesignatedStorage())
    t2 = Transmitter(p_uid)
    t3 = Transmitter(tp_uid)

    _add_connection(c_uid, p_uid)
    _add_connection(c_uid, tp_uid)

    # pkt = Packet(Action.ADD, receiver_uid=t_uid, payload=f"{p_uid}")
    # pkt2 = Packet(Action.ADD, receiver_uid=p_uid, payload=f"{t_uid}")
    # pkt3 = Packet(Action.ADD, receiver_uid=t_uid, payload=f"{tp_uid}")
    # pkt4 = Packet(Action.ADD, receiver_uid=tp_uid, payload=f"{t_uid}")
    # t._add_peer(pkt)
    # t2._add_peer(pkt2)
    # t._add_peer(pkt3)
    # t3._add_peer(pkt4)

    from pprint import pprint

    pprint(t.peers)
    print()
    pprint(t2)

    print()
    pprint(AddressMapper.address_to_stream)

    # chunked_data = split_data(data, (p_uid, tp_uid), 10)

    th1 = Thread(target=t.run)
    th2 = Thread(target=t2.run)
    th3 = Thread(target=t3.run)
    th1.start()
    th2.start()
    th3.start()

    pkt = Packet(Action.ADD, receiver_uid=c_uid, payload=f"{p_uid}")
    pkt2 = Packet(Action.ADD, receiver_uid=p_uid, payload=f"{c_uid}")
    pkt3 = Packet(Action.ADD, receiver_uid=c_uid, payload=f"{tp_uid}")
    pkt4 = Packet(Action.ADD, receiver_uid=tp_uid, payload=f"{c_uid}")

    t._add_peer(pkt)
    t2._add_peer(pkt2)
    t._add_peer(pkt3)
    t3._add_peer(pkt4)

    distributed_data = make_data_distribution(
        data,
        (p_uid, tp_uid),
        batch_size=batch_size,
    )
    distribute_data(distributed_data, t)

    # for who_uid, datas_to_store in chunked_data.items():
    #     for data_to_store in datas_to_store:
    #         t._packet_queue.put(
    #             Packet(
    #                 Action.STORE,
    #                 receiver_uid=who_uid,
    #                 payload=data_to_store,
    #             ),
    #         )
    #         # t.send(
    #         #     who_uid,
    #         #     Packet(
    #         #         Action.STORE,
    #         #         receiver_uid=who_uid,
    #         #         payload=data_to_store,
    #         #     ),
    #         # )
    # for who_uid in chunked_data:
    #     t._packet_queue.put(
    #         Packet(
    #             Action.STORE,
    #             receiver_uid=who_uid,
    #             payload="\x00",
    #         ),
    #     )
    #     # t.send(
    #     #     who_uid,
    #     #     Packet(
    #     #         Action.STORE,
    #     #         receiver_uid=who_uid,
    #     #         payload="\x00",
    #     #     ),
    #     # )

    # t.send(p_uid, Packet(Action.STORE, p_uid, "0000Yo,\x00"))
    # t.send(tp_uid, Packet(Action.STORE, tp_uid, "0001bitch!\x00"))
    # time.sleep(10)
    # print(f"{t3.storage.stored_data = }")
    # print(f"{t2.storage.stored_data = !r}")
    # st = defaultdict(str)
    # while (tt := t2.storage.give()) != EOS:
    #     tt_id = int(tt[:4])
    #     st[tt_id] = tt[4:]
    # print(f"{st = }")
    # print("".join(st.values()))
    # quit()

    assemble_data(t, uids)

    # t._packet_queue.put(Packet(Action.GIVE, p_uid, payload=f"{c_uid}"))
    # t._packet_queue.put(Packet(Action.GIVE, tp_uid, payload=f"{c_uid}"))

    # time.sleep(10)
    # t.send(p_uid, Packet(Action.GIVE, p_uid, c_uid))
    # # time.sleep(3)
    # t.send(tp_uid, Packet(Action.GIVE, tp_uid, c_uid))
    while not len(t.storage.stored_data) == sum(map(len, distributed_data.values())):
        print()
        print()
        print()
        print(f"{len(t.storage.stored_data) = }")
        print(f"{sum(map(len, distributed_data.values())) = }")
        print()
        print()
        print()
        time.sleep(3)

    a, b, c = starmap(TransmitterNode, zip((c_uid, p_uid, tp_uid), (0.1, 0.1, 0.1)))
    terminate_all(t, {b: [a, b], c: [a, c]})
    # t._terminate(Packet(Action.TERM, c_uid, ""))
    # t2._terminate(Packet(Action.TERM, p_uid, ""))
    # t3._terminate(Packet(Action.TERM, tp_uid, ""))
    th1.join()
    th2.join()
    th3.join()

    print()
    print()
    for key, dat in distributed_data.items():
        print(key)
        for dd in dat:
            print(f"\t{dd!r}")
    print()
    print(sum(map(len, distributed_data.values())))
    print()
    print(f"{t.storage.stored_data = }")
    st_data = t.storage.stored_data
    print(f"{sorted(st_data.items()) = }")
    res_data = "".join(d for k, d in sorted(st_data.items()))
    assert res_data == data, list(difflib.unified_diff(res_data, data))
    print(f"{res_data!r} YYYYYY")
