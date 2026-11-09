"""``catmod`` command-line interface."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer

from catmod.config import PortfolioConfig, list_presets, load_config, load_preset

app = typer.Typer(no_args_is_help=True, help="Open-source catastrophe modelling toolkit.")


@app.command()
def presets() -> None:
    """List built-in portfolio presets."""
    for name in list_presets():
        typer.echo(name)


@app.command()
def generate(
    out: Annotated[Path, typer.Option(help="Output directory")],
    preset: Annotated[str | None, typer.Option(help="Built-in preset name")] = None,
    config: Annotated[Path | None, typer.Option(exists=True, help="YAML config file")] = None,
    n: Annotated[int | None, typer.Option(help="Override number of locations")] = None,
    seed: Annotated[int | None, typer.Option(help="Override RNG seed")] = None,
    fmt: Annotated[list[str] | None, typer.Option("--format", help="parquet and/or csv")] = None,
) -> None:
    """Generate a synthetic OED portfolio."""
    from catmod.exposure import generate as gen
    from catmod.exposure.io import summarize, write_portfolio

    fmt = fmt or ["parquet", "csv"]
    if (preset is None) == (config is None):
        raise typer.BadParameter("give exactly one of --preset or --config")
    cfg = load_preset(preset) if preset else load_config(config)  # type: ignore[arg-type]
    overrides = {k: v for k, v in {"n_locations": n, "seed": seed}.items() if v is not None}
    cfg = PortfolioConfig.model_validate({**cfg.model_dump(), **overrides})
    bad = set(fmt) - {"parquet", "csv"}
    if bad:
        raise typer.BadParameter(f"unknown format(s): {sorted(bad)}")

    t0 = time.perf_counter()
    p = gen(cfg)
    t1 = time.perf_counter()
    paths = write_portfolio(p, out, tuple(fmt))  # type: ignore[arg-type]
    s = summarize(p.locations, p.accounts)
    typer.echo(
        f"generated {s.n_locations:,} locations / {s.n_accounts:,} accounts, "
        f"TIV {s.tiv_total / 1e9:,.2f}bn {cfg.currency} in {(t1 - t0) * 1e3:,.0f} ms"
    )
    for path in paths:
        typer.echo(f"  wrote {path}")


@app.command("export-schemas")
def export_schemas(
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("../schemas"),
) -> None:
    """Write JSON Schema + OpenAPI contracts."""
    from catmod.schema_export import export_all

    for path in export_all(out):
        typer.echo(f"wrote {path}")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("catmod.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
