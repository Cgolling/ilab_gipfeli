"""Plotly-based interactive visualization for GraphNav maps."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import plotly.graph_objects as go

import numpy as np

from src.map_viewer.transformer import (
    compute_edge_lines,
    compute_fiducial_positions,
    compute_point_clouds,
    compute_waypoint_positions,
)
from src.spot.spot_controller import id_to_short_code, WAYPOINTS

# Reverse mapping: short_code -> location name (e.g., "al" -> "Aula")
SHORT_CODE_TO_LOCATION = {sc: name.capitalize() for name, sc in WAYPOINTS.items()}

if TYPE_CHECKING:
    from src.map_viewer.loader import MapData


@dataclass
class WaypointInfo:
    """Extracted waypoint metadata for display."""

    id: str
    short_code: str | None
    name: str
    position: tuple[float, float, float]
    fiducial_count: int
    location_name: str | None = None  # Meaningful name from WAYPOINTS (e.g., "Aula")


def extract_waypoint_info(
    map_data: "MapData",
    positions: dict[str, tuple[float, float, float]],
) -> list[WaypointInfo]:
    """
    Extract displayable metadata from all waypoints.

    Args:
        map_data: Loaded map data
        positions: Dict mapping waypoint_id to position

    Returns:
        List of WaypointInfo for each waypoint with a computed position
    """
    infos: list[WaypointInfo] = []

    for waypoint_id, position in positions.items():
        waypoint = map_data.waypoints.get(waypoint_id)
        if waypoint is None:
            continue

        # Count fiducials in this waypoint's snapshot
        fiducial_count = 0
        snapshot = map_data.waypoint_snapshots.get(waypoint.snapshot_id)
        if snapshot:
            for obj in snapshot.objects:
                if obj.HasField("apriltag_properties"):
                    fiducial_count += 1

        short_code = id_to_short_code(waypoint_id)
        location_name = SHORT_CODE_TO_LOCATION.get(short_code) if short_code else None

        infos.append(
            WaypointInfo(
                id=waypoint_id,
                short_code=short_code,
                name=waypoint.annotations.name or "",
                position=position,
                fiducial_count=fiducial_count,
                location_name=location_name,
            )
        )

    return infos


def create_figure(
    map_data: "MapData",
    title: str = "GraphNav Map",
    highlight_waypoints: list[str] | None = None,
    show_edges: bool = True,
    show_fiducials: bool = True,
    show_waypoint_labels: bool = False,
    show_point_clouds: bool = False,
    use_anchoring: bool = True,
) -> go.Figure:
    """
    Create interactive 3D Plotly figure for map visualization.

    Args:
        map_data: Loaded map data
        title: Figure title
        highlight_waypoints: List of short codes or names to highlight (green)
        show_edges: Whether to show edge lines
        show_fiducials: Whether to show fiducial markers
        show_waypoint_labels: Whether to show labels on all waypoints
        show_point_clouds: Whether to show point cloud data
        use_anchoring: Use anchor seed frame if available

    Returns:
        Plotly Figure object ready for display
    """
    # Compute positions
    positions = compute_waypoint_positions(map_data, use_anchoring=use_anchoring)
    waypoint_infos = extract_waypoint_info(map_data, positions)

    # Prepare highlight set (normalize to lowercase)
    highlight_set: set[str] = set()
    if highlight_waypoints:
        for h in highlight_waypoints:
            highlight_set.add(h.lower())

    # Separate waypoints into highlighted and regular
    highlighted_infos: list[WaypointInfo] = []
    regular_infos: list[WaypointInfo] = []

    for info in waypoint_infos:
        is_highlighted = (
            (info.short_code and info.short_code.lower() in highlight_set)
            or (info.name and info.name.lower() in highlight_set)
        )
        if is_highlighted:
            highlighted_infos.append(info)
        else:
            regular_infos.append(info)

    # Create figure
    fig = go.Figure()

    # Add point clouds first (so they're behind everything)
    if show_point_clouds:
        point_cloud_data = compute_point_clouds(map_data, use_anchoring=use_anchoring)
        if len(point_cloud_data) > 0:
            _add_point_clouds_to_figure(fig, point_cloud_data)

    # Add edges (behind waypoints)
    if show_edges:
        edge_lines = compute_edge_lines(map_data, positions)
        _add_edges_to_figure(fig, edge_lines)

    # Add regular waypoints (blue, smaller)
    if regular_infos:
        _add_waypoints_to_figure(
            fig,
            regular_infos,
            color="rgb(65, 105, 225)",  # Royal blue
            name="Waypoints",
            marker_size=6,
            show_labels=show_waypoint_labels,
        )

    # Add highlighted waypoints (green, larger, always with labels) - delivery locations
    if highlighted_infos:
        _add_waypoints_to_figure(
            fig,
            highlighted_infos,
            color="rgb(50, 205, 50)",  # Lime green
            name="Delivery Locations",
            marker_size=14,
            show_labels=True,  # Always show labels for delivery locations
        )

    # Add fiducials (orange)
    if show_fiducials:
        fiducial_positions = compute_fiducial_positions(map_data, positions)
        if fiducial_positions:
            _add_fiducials_to_figure(fig, fiducial_positions)

    # Build toggle buttons for each trace type
    toggle_buttons = _create_toggle_buttons(fig)

    # Configure layout
    fig.update_layout(
        title=title,
        scene=dict(
            xaxis_title="X (m)",
            yaxis_title="Y (m)",
            zaxis_title="Z (m)",
            aspectmode="data",
        ),
        showlegend=True,
        legend=dict(
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=0.01,
            itemclick="toggle",
            itemdoubleclick="toggleothers",
        ),
        margin=dict(l=0, r=0, t=80, b=0),
        updatemenus=toggle_buttons,
    )

    return fig


def _create_toggle_buttons(fig: go.Figure) -> list[dict]:
    """
    Create a dropdown menu for visibility control.

    Returns a list of updatemenus configurations for Plotly.
    Uses explicit visibility arrays since Plotly doesn't support "toggle".
    """
    trace_names = [trace.name for trace in fig.data]
    num_traces = len(trace_names)

    buttons = [
        dict(
            label="All Visible",
            method="restyle",
            args=[{"visible": [True] * num_traces}],
        ),
        dict(
            label="All Hidden",
            method="restyle",
            args=[{"visible": ["legendonly"] * num_traces}],
        ),
    ]

    # Add "Only X" options - show only one trace type
    for i, name in enumerate(trace_names):
        visible = ["legendonly"] * num_traces
        visible[i] = True
        buttons.append(
            dict(
                label=f"Only {name}",
                method="restyle",
                args=[{"visible": visible}],
            )
        )

    # Add "Hide X" options - hide specific trace type
    for i, name in enumerate(trace_names):
        visible = [True] * num_traces
        visible[i] = "legendonly"
        buttons.append(
            dict(
                label=f"Hide {name}",
                method="restyle",
                args=[{"visible": visible}],
            )
        )

    return [
        dict(
            type="dropdown",
            direction="down",
            buttons=buttons,
            pad={"r": 10, "t": 10},
            showactive=True,
            x=0.0,
            xanchor="left",
            y=1.15,
            yanchor="top",
        )
    ]


def _add_waypoints_to_figure(
    fig: go.Figure,
    infos: list[WaypointInfo],
    color: str,
    name: str,
    marker_size: int = 8,
    show_labels: bool = True,
) -> None:
    """Add waypoint markers with hover information."""
    x = [info.position[0] for info in infos]
    y = [info.position[1] for info in infos]
    z = [info.position[2] for info in infos]

    # Build hover text and display labels
    hover_texts = []
    display_labels = []
    for info in infos:
        # Use location name if available (e.g., "Aula"), otherwise use annotation name
        display_name = info.location_name or info.name or info.short_code or ""
        display_labels.append(display_name if show_labels else "")

        # Build detailed hover text
        text = f"<b>{display_name}</b><br>"
        if info.location_name:
            text += f"Location: {info.location_name}<br>"
        text += f"Name: {info.name}<br>"
        text += f"ID: {info.id}<br>"
        if info.short_code:
            text += f"Short code: {info.short_code}<br>"
        text += f"Position: ({info.position[0]:.2f}, {info.position[1]:.2f}, {info.position[2]:.2f})<br>"
        text += f"Fiducials: {info.fiducial_count}"
        hover_texts.append(text)

    mode = "markers+text" if show_labels else "markers"
    fig.add_trace(
        go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode=mode,
            marker=dict(size=marker_size, color=color, opacity=0.9),
            text=display_labels,
            textposition="top center",
            textfont=dict(size=14, color="black"),
            hovertext=hover_texts,
            hoverinfo="text",
            name=name,
        )
    )


def _add_edges_to_figure(
    fig: go.Figure,
    edge_lines: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> None:
    """Add edge lines to figure."""
    # Build coordinate lists with None separators for disconnected lines
    x: list[float | None] = []
    y: list[float | None] = []
    z: list[float | None] = []

    for start, end in edge_lines:
        x.extend([start[0], end[0], None])
        y.extend([start[1], end[1], None])
        z.extend([start[2], end[2], None])

    fig.add_trace(
        go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode="lines",
            line=dict(color="rgb(150, 150, 150)", width=2),
            hoverinfo="skip",
            name="Edges",
        )
    )


def _add_fiducials_to_figure(
    fig: go.Figure,
    fiducial_positions: dict[str, tuple[float, float, float]],
) -> None:
    """Add fiducial markers to figure."""
    x = [pos[0] for pos in fiducial_positions.values()]
    y = [pos[1] for pos in fiducial_positions.values()]
    z = [pos[2] for pos in fiducial_positions.values()]
    ids = list(fiducial_positions.keys())

    hover_texts = [
        f"<b>Fiducial {fid}</b><br>Position: ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
        for fid, pos in fiducial_positions.items()
    ]

    fig.add_trace(
        go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode="markers+text",
            marker=dict(size=10, color="rgb(255, 165, 0)", symbol="diamond", opacity=0.9),
            text=ids,
            textposition="top center",
            textfont=dict(size=12),
            hovertext=hover_texts,
            hoverinfo="text",
            name="Fiducials",
        )
    )


def _add_point_clouds_to_figure(
    fig: go.Figure,
    point_cloud_data: np.ndarray,
) -> None:
    """
    Add point cloud visualization to figure.

    Args:
        fig: Plotly figure
        point_cloud_data: Nx4 array of (x, y, z, height)
    """
    x = point_cloud_data[:, 0]
    y = point_cloud_data[:, 1]
    z = point_cloud_data[:, 2]
    heights = point_cloud_data[:, 3]

    fig.add_trace(
        go.Scatter3d(
            x=x,
            y=y,
            z=z,
            mode="markers",
            marker=dict(
                size=1,
                color=heights,
                colorscale="Viridis",
                opacity=0.6,
                colorbar=dict(
                    title="Height (m)",
                    x=1.02,
                    len=0.5,
                ),
            ),
            hoverinfo="skip",
            name="Point Cloud",
        )
    )


def show_figure(fig: go.Figure) -> None:
    """Display figure in browser."""
    fig.show()


def export_html(fig: go.Figure, output_path: str) -> None:
    """Export figure as standalone HTML file."""
    fig.write_html(output_path, include_plotlyjs=True, full_html=True)
