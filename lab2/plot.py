from collections.abc import Iterable
from pathlib import Path

import attrs
import matplotlib.pyplot as plt
from network import Network, Node


@attrs.define
class Plotter:
    fig: plt.Figure = attrs.field(init=False)
    ax: plt.Axes = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.fig, self.ax = plt.subplots()

    def plot_points(
        self,
        nodes: Iterable[Node],
    ):
        xs = [node.pos.x for node in nodes]
        ys = [node.pos.y for node in nodes]
        self.ax.scatter(xs, ys)
        for node in nodes:
            self.ax.text(node.pos.x, node.pos.y + 0.08, f"{node.idx}")
        return self

    def plot_graph(
        self,
        net: Network,
    ):
        for node, neighbours in net.graph.items():
            for neighbour in neighbours:
                self.ax.plot(
                    (node.pos.x, neighbour.pos.x),
                    (node.pos.y, neighbour.pos.y),
                    "r",
                    zorder=-1,
                )
        return self

    def save(self, output_path: Path) -> None:
        self.fig.savefig(output_path.with_suffix(".png"))
