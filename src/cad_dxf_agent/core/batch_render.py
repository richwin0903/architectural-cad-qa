"""Batch adjacent line strokes while preserving CAD draw order and fills."""

from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from matplotlib.collections import LineCollection


class BatchMatplotlibBackend(MatplotlibBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines = []
        self._stroke = None

    def _flush(self):
        if self._lines:
            color, width = self._stroke
            self.ax.add_collection(
                LineCollection(
                    self._lines,
                    colors=color,
                    linewidths=width,
                    zorder=self._get_z(),
                    capstyle="butt",
                )
            )
            self._lines = []

    def draw_line(self, start, end, properties):
        if start.isclose(end):
            self.draw_point(start, properties)
            return
        stroke = (properties.color, self.get_lineweight(properties))
        if stroke != self._stroke:
            self._flush()
            self._stroke = stroke
        self._lines.append(((start.x, start.y), (end.x, end.y)))

    def draw_solid_lines(self, lines, properties):
        for start, end in lines:
            self.draw_line(start, end, properties)

    def draw_point(self, pos, properties):
        self._flush()
        super().draw_point(pos, properties)

    def draw_path(self, path, properties):
        self._flush()
        super().draw_path(path, properties)

    def draw_filled_paths(self, paths, properties):
        self._flush()
        super().draw_filled_paths(paths, properties)

    def draw_filled_polygon(self, points, properties):
        self._flush()
        super().draw_filled_polygon(points, properties)

    def draw_image(self, image_data, properties):
        self._flush()
        super().draw_image(image_data, properties)

    def finalize(self):
        self._flush()
        super().finalize()


def save_layout(layout, filename, *, dpi, background):
    """Use a non-GUI figure and the same ezdxf renderer, with batched line strokes."""
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, ImagePolicy
    from ezdxf.addons.drawing.properties import LayoutProperties
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    properties = LayoutProperties.from_layout(layout)
    properties.set_colors(background)
    try:
        Frontend(
            RenderContext(layout.doc),
            BatchMatplotlibBackend(ax),
            config=Configuration(image_policy=ImagePolicy.PROXY),
        ).draw_layout(layout, finalize=True, layout_properties=properties)
        fig.savefig(filename, dpi=dpi, facecolor=ax.get_facecolor(), transparent=True)
    finally:
        fig.clear()
