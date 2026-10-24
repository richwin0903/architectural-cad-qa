"""Batch adjacent line strokes while preserving CAD draw order and fills."""

from ezdxf.addons.drawing.matplotlib import MatplotlibBackend, to_matplotlib_path
from matplotlib.collections import LineCollection, PathCollection
from matplotlib.path import Path as MplPath


class BatchMatplotlibBackend(MatplotlibBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines = []
        self._stroke = None
        self._paths = []
        self._path_style = None

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

    def _flush_paths(self):
        if self._paths:
            color, width, filled = self._path_style
            self.ax.add_collection(
                PathCollection(
                    self._paths,
                    facecolors=color if filled else "none",
                    edgecolors="none" if filled else color,
                    linewidths=width,
                    zorder=self._get_z(),
                    capstyle="butt",
                )
            )
            self._paths = []

    def _queue_path(self, path, properties, filled=False):
        self._flush()
        style = (properties.color, 0 if filled else self.get_lineweight(properties), filled)
        if style != self._path_style or len(self._paths) >= 1024:
            self._flush_paths()
            self._path_style = style
        self._paths.append(path)

    def draw_line(self, start, end, properties):
        self._flush_paths()
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
        self._flush_paths()
        super().draw_point(pos, properties)

    def draw_path(self, path, properties):
        self._queue_path(to_matplotlib_path([path]), properties)

    def draw_filled_paths(self, paths, properties):
        self._queue_path(to_matplotlib_path(paths, detect_holes=True), properties, filled=True)

    def draw_filled_polygon(self, points, properties):
        vertices = [(p.x, p.y) for p in points.vertices()]
        if vertices:
            self._queue_path(MplPath(vertices + vertices[:1], closed=True), properties, filled=True)

    def draw_image(self, image_data, properties):
        self._flush()
        self._flush_paths()
        super().draw_image(image_data, properties)

    def finalize(self):
        self._flush()
        self._flush_paths()
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
