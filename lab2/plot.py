from collections.abc import Iterable
from pathlib import Path

import attrs
import matplotlib.pyplot as plt
from network import Network, Point


@attrs.define
class Plotter:
    fig: plt.Figure = attrs.field(init=False)
    ax: plt.Axes = attrs.field(init=False)

    def __attrs_post_init__(self):
        self.fig, self.ax = plt.subplots()

    def plot_points(
        self,
        pts: Iterable[Point],
    ):
        xs = [pt.x for pt in pts]
        ys = [pt.y for pt in pts]
        self.ax.scatter(xs, ys)
        for i, p in enumerate(pts):
            self.ax.text(p.x, p.y + 0.08, f"{i}")
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
