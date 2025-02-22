import heapq
import math
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, MutableSequence, Sequence
from itertools import permutations
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, TextIO

import attrs

from .high_transfer import UID


@attrs.frozen
class Point:
    x: float
    y: float

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)


class NodeProto(Hashable, Protocol):
    @property
    def uid(self) -> UID: ...
    @property
    def pt(self) -> Point: ...
    def __lt__(self, other: "NodeProto") -> bool: ...
    @staticmethod
    def dist(a: "NodeProto", b: "NodeProto") -> float: ...


# @attrs.frozen(order=True)
# class Node:
#     idx: int
#     pos: Point

#     def __repr__(self):
#         return f"{self.idx}"

#     @staticmethod
#     def dist(a: "Node", b: "Node") -> float:
#         return math.hypot(*attrs.astuple(a.pos - b.pos))


type Dists = Mapping[NodeProto, float]
type Paths = Mapping[NodeProto, Sequence[NodeProto]]
type Graph = Mapping[NodeProto, Dists]


@attrs.define
class Network:
    nodes: MutableSequence[NodeProto]
    max_distance: float
    graph: Graph = attrs.field(init=False)

    def _make_graph(self, nodes: Iterable[NodeProto], max_distance: float) -> Graph:
        graph: Graph = defaultdict(dict)
        for node, neighbor in permutations(nodes, 2):
            if (dist := type(node).dist(node, neighbor)) <= max_distance:
                graph[node][neighbor] = dist
        return graph

    def __attrs_post_init__(self):
        self.graph = self._make_graph(self.nodes, self.max_distance)

    @classmethod
    def from_nodes(cls, nodes: MutableSequence[NodeProto], max_distance: float):
        return cls(nodes, max_distance)

    def remove_node(self, node_idx: int) -> None:
        del self.nodes[node_idx]
        self.graph = self._make_graph(self.nodes, self.max_distance)

    def ospf(self, from_node: NodeProto):
        return self.dijkstra(from_node)
        # return tuple(map(self.dijkstra, self.graph))

    def dijkstra(
        self,
        start: NodeProto,
    ) -> Paths:
        # Priority queue to store (cost, node)
        pq = [(0.0, start)]
        # Dictionary to store the shortest distance to each node
        shortest_paths = {node: float("inf") for node in self.graph}
        shortest_paths[start] = 0.0
        # Dictionary to store the actual paths
        paths: Paths = {node: [] for node in self.graph}
        paths[start] = [start]

        while pq:
            current_distance, current_node = heapq.heappop(pq)

            # Skip processing if we already found a shorter path
            if current_distance > shortest_paths[current_node]:
                continue

            for neighbor, weight in self.graph[current_node].items():
                distance = current_distance + weight

                # If found a shorter path, update and push to priority queue
                if distance < shortest_paths[neighbor]:
                    shortest_paths[neighbor] = distance
                    heapq.heappush(pq, (distance, neighbor))
                    # Store the path by extending the current node's path
                    paths[neighbor] = paths[current_node] + [neighbor]

        print(shortest_paths)
        return paths
