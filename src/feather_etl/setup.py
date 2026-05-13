"""`feather setup` core — orchestration without Typer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from feather_etl.config import FeatherConfig
from feather_etl.transforms import TransformResult


@dataclass
class SetupResult:
    """Result of running `feather setup` against a loaded config."""

    state_db_path: Path
    destination_path: Path
    transform_results: list[TransformResult] | None  # None if no transforms found


def run_setup(cfg: FeatherConfig, force_views: bool = False) -> SetupResult:
    """Initialize state DB, create destination schemas, execute transforms.

    Returns a ``SetupResult`` describing what was created. Transforms are
    executed if any are discovered in ``<config_dir>/transforms/``. Pass
    ``force_views=True`` to create all transforms as VIEWs, skipping gold
    materialization.
    """
    from feather_etl.destinations.duckdb import DuckDBDestination
    from feather_etl.state import StateManager
    from feather_etl.transforms import discover_transforms

    state_path = cfg.config_dir / "feather_state.duckdb"
    sm = StateManager(state_path)
    sm.init_state()

    dest = DuckDBDestination(path=cfg.destination.path)
    dest.setup_schemas()

    transform_results: list[TransformResult] | None = None
    transforms = discover_transforms(cfg.config_dir)
    if transforms:
        from feather_etl.transforms import run_transforms
        transform_results = run_transforms(cfg, force_views=force_views)

    return SetupResult(
        state_db_path=state_path,
        destination_path=cfg.destination.path,
        transform_results=transform_results,
    )
