import heapq
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import permutations
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self, TextIO

import attrs


@attrs.frozen
class Point:
    x: float
    y: float

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)


@attrs.frozen(order=True)
class Node:
    idx: int
    pos: Point

    def __repr__(self):
        return f"{self.idx}"

    @staticmethod
    def dist(a: "Node", b: "Node") -> float:
        return math.hypot(*attrs.astuple(a.pos - b.pos))


class PrinterProto(Protocol):
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool: ...
    def __call__(self, what: str = "") -> None: ...


class Printer:
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    def __call__(self, what: str = "") -> None:
        print(what)


@attrs.define
class FilePrinter:
    file: Path
    _opened_file: TextIO = attrs.field(init=False, alias="_opened_file")

    def __enter__(self) -> Self:
        self._opened_file = self.file.open("w")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self._opened_file.close()
        return False

    def __call__(self, what: str = "") -> None:
        print(what, file=self._opened_file)


def print_path(
    start_node: Node,
    paths: Mapping[Node, Sequence[Node]],
    printer: PrinterProto,
):
    for other_node, path in paths.items():
        printer(f"{start_node} -> {other_node}: {path}")
    printer()


type Dists = Mapping[Node, float]
type Paths = Mapping[Node, Sequence[Node]]
type Graph = Mapping[Node, Dists]


@attrs.define
class Network:
    nodes: MutableSequence[Node]
    max_distance: float
    graph: Graph = attrs.field(init=False)

    def _make_graph(self, nodes: Iterable[Node], max_distance: float) -> Graph:
        graph: Graph = defaultdict(dict)
        for node, neighbor in permutations(nodes, 2):
            if (dist := Node.dist(node, neighbor)) <= max_distance:
                graph[node][neighbor] = dist
        return graph

    def __attrs_post_init__(self):
        self.graph = self._make_graph(self.nodes, self.max_distance)

    @classmethod
    def from_points(cls, points: Iterable[Point], max_distance: float):
        nodes = [Node(idx, point) for idx, point in enumerate(points)]
        return cls(nodes, max_distance)

    def remove_node(self, node_idx: int) -> None:
        del self.nodes[node_idx]
        self.graph = self._make_graph(self.nodes, self.max_distance)

    def ospf(self, printer: PrinterProto | None = None):
        with printer or Printer() as printer:
            for node in self.graph:
                _, paths = self.dijkstra(node)
                print_path(node, paths, printer)

    def dijkstra(
        self,
        start: Node,
    ) -> tuple[
        Dists,
        Paths,
    ]:
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

        return shortest_paths, paths
