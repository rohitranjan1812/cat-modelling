"""``catmod`` command-line interface."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Any

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


def _exposure(portfolio: Path) -> Any:
    from catmod.engine.context import Exposure

    return Exposure.from_dir(portfolio)


def _analysis(preset: str, config: Path | None) -> Any:
    import yaml

    from catmod.engine.drill import analysis_presets
    from catmod.engine.runner import AnalysisConfig

    if config:
        return AnalysisConfig.model_validate(yaml.safe_load(config.read_text()))
    presets = analysis_presets()
    if preset not in presets:
        raise typer.BadParameter(f"unknown preset {preset!r}; have {sorted(presets)}")
    return presets[preset]


@app.command()
def run(
    portfolio: Annotated[Path, typer.Option(exists=True, help="Portfolio dir (Parquet)")],
    out: Annotated[Path, typer.Option(help="Output directory for run tables")],
    preset: Annotated[str, typer.Option(help="Analysis preset")] = "standard",
    config: Annotated[Path | None, typer.Option(exists=True, help="Analysis YAML")] = None,
) -> None:
    """Run a stochastic analysis and write ELT/YLT/metrics."""
    from catmod.engine.runner import run_analysis

    cfg = _analysis(preset, config)
    x = _exposure(portfolio)
    res = run_analysis(x, cfg, lambda f, m: typer.echo(f"\r{f:6.1%} {m:<40}", nl=False))
    typer.echo("")
    res.save(out)
    for name, m in res.summary.metrics.items():
        typer.echo(
            f"{name:>6}: AAL {m.aal:>16,.0f}  OEP100 {m.oep.get('100', 0):>16,.0f}  "
            f"OEP250 {m.oep.get('250', 0):>16,.0f}  TVaR99 {m.tvar.get('0.99', 0):>16,.0f}"
        )
    typer.echo(f"wrote {out} in {res.summary.elapsed_s}s")


@app.command()
def benchmark(
    portfolio: Annotated[Path, typer.Option(exists=True, help="Portfolio dir (Parquet)")],
    preset: Annotated[str, typer.Option(help="Analysis preset")] = "quick",
    rho: Annotated[float, typer.Option(help="Damage correlation strength")] = 0.3,
) -> None:
    """Compare damage-correlation models on the same portfolio and catalogue.

    AAL must agree (copulas preserve marginals); the tail metrics show how much the dependence
    assumption matters for this portfolio.
    """
    from catmod.engine.runner import AnalysisConfig, run_analysis

    base = _analysis(preset, None)
    x = _exposure(portfolio)
    typer.echo(f"{'model':<14}{'AAL':>16}{'OEP100':>16}{'OEP250':>16}{'TVaR99':>16}{'sd':>16}")
    for model in ("independent", "one_factor", "spatial"):
        cfg = AnalysisConfig.model_validate(
            {**base.model_dump(), "damage_correlation": {"model": model, "rho": rho}}
        )
        m = run_analysis(x, cfg).summary.metrics["gross"]
        typer.echo(
            f"{model:<14}{m.aal:>16,.0f}{m.oep['100']:>16,.0f}{m.oep['250']:>16,.0f}"
            f"{m.tvar['0.99']:>16,.0f}{m.sd:>16,.0f}"
        )


@app.command()
def scenario(
    portfolio: Annotated[Path, typer.Option(exists=True, help="Portfolio dir (Parquet)")],
    peril: Annotated[str, typer.Option(help="QEQ or WTC")],
    event: Annotated[
        str, typer.Option(help='Event spec JSON, e.g. {"lat":34,"lon":-118.3,"magnitude":7}')
    ],
    samples: int = 200,
) -> None:
    """Run one deterministic event with secondary uncertainty; print loss statistics."""
    import json

    from catmod.engine.drill import drill
    from catmod.engine.runner import AnalysisConfig, Engine

    x = _exposure(portfolio)
    eng = Engine(x, AnalysisConfig(perils=[peril], n_samples=samples, workers=1))
    hz = eng.hazards[peril]
    d = drill(
        x,
        eng.event_loss(hz.event_from_spec(json.loads(event), 9_000_001), samples),
        hz.unit,
        top=10,
    )
    typer.echo(f"{d.name}: {d.n_affected:,} locations affected")
    for k in ("gu", "gross"):
        st = getattr(d, k)
        typer.echo(
            f"  {k:>5}: mean {st.mean:,.0f}  p50 {st.p50:,.0f}  p90 {st.p90:,.0f}  "
            f"p99 {st.p99:,.0f}"
        )


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
