"""Vector batching must preserve curves, fill holes and draw order."""

import ezdxf
import numpy as np
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from cad_dxf_agent.core.batch_render import BatchMatplotlibBackend


def test_batched_paths_match_reference_renderer():
    doc = ezdxf.new()
    model = doc.modelspace()
    model.add_solid([(0, 0), (8, 0), (0, 8), (8, 8)], dxfattribs={"color": 1})
    hatch = model.add_hatch(color=3)
    hatch.paths.add_polyline_path([(1, 1), (7, 1), (7, 7), (1, 7)], is_closed=True, flags=1)
    hatch.paths.add_polyline_path([(3, 3), (3, 5), (5, 5), (5, 3)], is_closed=True, flags=0)
    for offset in [0, 2]:
        model.add_arc((4 + offset, 4), 3, 10, 250, dxfattribs={"color": 7})
        model.add_circle((4 + offset, 4), 1, dxfattribs={"color": 5})
    model.add_line((0, 0), (9, 9), dxfattribs={"color": 7})
    model.add_ellipse((4, 4), major_axis=(3, 1), ratio=0.5, dxfattribs={"color": 2})

    def render(backend):
        fig = Figure(figsize=(4, 4), dpi=100)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_axes((0, 0, 1, 1))
        Frontend(
            RenderContext(doc),
            backend(ax, adjust_figure=False),
            config=Configuration(background_policy=BackgroundPolicy.WHITE),
        ).draw_layout(model)
        ax.set_xlim(-1, 10)
        ax.set_ylim(-1, 10)
        canvas.draw()
        pixels = np.asarray(canvas.buffer_rgba()).copy().astype(float)
        fig.clear()
        return pixels

    reference, batched = render(MatplotlibBackend), render(BatchMatplotlibBackend)
    assert np.abs(reference - batched).mean() < 3
    # The hole and overlaid strokes remain visible, not a flattened filled rectangle.
    assert np.unique(batched.reshape(-1, 4), axis=0).shape[0] > 100
