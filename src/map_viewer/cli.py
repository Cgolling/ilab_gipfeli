"""Command-line interface for GraphNav map viewer."""

import argparse
import logging
import sys

from src.logging_config import setup_logging
from src.map_viewer.loader import load_map
from src.map_viewer.viewer import create_figure, export_html, show_figure


def main() -> None:
    """Main entry point for map viewer CLI."""
    parser = argparse.ArgumentParser(
        description="GraphNav Map Viewer - Interactive visualization for SPOT maps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # View map in browser
  uv run python -m src.map_viewer maps/map_catacombs_01/

  # Use anchoring and highlight delivery waypoints
  uv run python -m src.map_viewer maps/map_catacombs_01/ -a --highlight al tv oh cw

  # Show all waypoint labels
  uv run python -m src.map_viewer maps/map_catacombs_01/ -a --show-labels

  # Show point cloud data
  uv run python -m src.map_viewer maps/map_catacombs_01/ -a --show-point-clouds

  # Export to HTML file
  uv run python -m src.map_viewer maps/map_catacombs_01/ --export map.html
        """,
    )

    parser.add_argument(
        "path",
        type=str,
        help="Path to GraphNav map directory",
    )
    parser.add_argument(
        "-a",
        "--anchoring",
        action="store_true",
        help="Use anchoring (seed frame) if available",
    )
    parser.add_argument(
        "--export",
        type=str,
        metavar="FILE",
        help="Export to HTML file instead of opening browser",
    )
    parser.add_argument(
        "--highlight",
        type=str,
        nargs="+",
        metavar="CODE",
        help="Waypoint short codes or names to highlight (e.g., al tv)",
    )
    parser.add_argument(
        "--no-edges",
        action="store_true",
        help="Hide edge connections",
    )
    parser.add_argument(
        "--no-fiducials",
        action="store_true",
        help="Hide fiducial markers",
    )
    parser.add_argument(
        "--show-labels",
        action="store_true",
        help="Show labels on all waypoints (default: only delivery locations)",
    )
    parser.add_argument(
        "--show-point-clouds",
        action="store_true",
        help="Show point cloud data (sampled for performance)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Custom title for the visualization",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging()
    if args.verbose:
        logging.getLogger("src.map_viewer").setLevel(logging.DEBUG)

    logger = logging.getLogger(__name__)

    # Load map
    try:
        logger.info(f"Loading map from {args.path}")
        map_data = load_map(args.path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Create figure
    title = args.title or f"GraphNav Map: {args.path}"
    fig = create_figure(
        map_data,
        title=title,
        highlight_waypoints=args.highlight,
        show_edges=not args.no_edges,
        show_fiducials=not args.no_fiducials,
        show_waypoint_labels=args.show_labels,
        show_point_clouds=args.show_point_clouds,
        use_anchoring=args.anchoring,
    )

    # Display or export
    if args.export:
        logger.info(f"Exporting to {args.export}")
        export_html(fig, args.export)
        print(f"Exported to {args.export}")
    else:
        logger.info("Opening in browser")
        show_figure(fig)


if __name__ == "__main__":
    main()
