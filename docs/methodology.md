# Methodology

catmod follows the standard four-module catastrophe model chain. Every stochastic component is
**pluggable** (entry-point registries, see `backend/src/catmod/engine/registry.py`) and every
parameter is **declarative** (`schemas/analysis-config.schema.json`).

> **Model status.** Hazard source models, GMM coefficients, landfall climatology and vulnerability
> curves are *illustrative*, calibrated to plausible orders of magnitude so the platform behaves
> realistically. They are not a licensed or peer-reviewed view of risk. Swap them via config or
> plugins before using results for pricing, reserving or capital.

## 1. Hazard

### Earthquake (QEQ)

* **Sources**: faults (polyline traces) and area zones (`engine/eq.py: DEFAULT_SOURCES`).
* **Magnitudes**: doubly-truncated Gutenberg–Richter, *enumerated* in bins of width `dm`:

  $$P(m_{lo}\le M<m_{hi}) = \frac{10^{-b(m_{lo}-M_{min})}-10^{-b(m_{hi}-M_{min})}}{1-10^{-b(M_{max}-M_{min})}}$$

* **Ruptures**: Wells & Coppersmith (1994) length $\log_{10}L = -3.22 + 0.69M$; ruptures slide along
  faults in `position_step_km` steps; area zones use a deterministic sunflower lattice with
  golden-angle strikes. Event rate = source rate × bin probability / positions, so rates are exact
  and the catalogue is identical on every machine.
* **Ground motion (PGA, g)**:

  $$\ln \mathrm{PGA} = a + b(M'-6) + c(M'-6)^2 - \ln\sqrt{R^2+h^2} - d\sqrt{R^2+h^2} + s\ln\frac{V_{s30}}{760}$$

  with $M'=\min(M, m_{sat})$, $R=\sqrt{R_{JB}^2 + z_{tor}^2}$, between-event $\tau$, within-event $\phi$.
  Defaults reproduce NGA-like medians (M6 @ 10 km ≈ 0.20 g, M7 @ 10 km ≈ 0.35 g on rock; tested).

### Tropical cyclone (WTC)

* **Landfall catalogue**: enumerated over a coastal gate (Texas → North Carolina) × intensity bins
  × heading offsets × Rmax quantiles. Annual landfall rate is distributed by a piecewise gate density
  and $P(V)\propto e^{-(V-V_{min})/\text{scale}}$.
* **Wind field** (1-min sustained, m/s): Holland (1980) in normalised form, exact at $r=R_{max}$:

  $$V(r)=V_{max}(t)\sqrt{(R_{max}/r)^B\,e^{1-(R_{max}/r)^B}} + \tfrac12 V_t\cos\!\big(\theta-(\psi+90^\circ)\big)$$

  (forward-motion asymmetry, strongest right of track). $R_{max}$ median from Willoughby et al. (2006).
* **Inland decay**: Kaplan & DeMaria (1995), $V(t)=V_b+(R V_0-V_b)e^{-\alpha t}$.
* **Site intensity** = max over hourly positions. *All* in-reach positions are evaluated: with decay,
  a site's peak can precede closest approach; a closest-approach shortcut under-estimated inland
  winds by up to 38% and was rejected (regression test vs great-circle brute force, < 1.5% error).
* **Arbitrary tracks** (scenario lab, NHC live): landfall detected by segment intersection with the
  coastal gate; decay applied after the first crossing.

## 2. Spatial correlation of hazard residuals (pluggable `SpatialField`)

Target: $\rho(h)=e^{-3h/r}$ (Jayaram & Baker 2009 form).

| model | method | cost | use |
|---|---|---|---|
| `independent` | i.i.d. | $O(n)$ | lower bound on clustering |
| `cholesky` | exact $LL^\top=C$ | $O(n^3)$, capped | validation |
| `fft` (default) | circulant embedding on a grid | $O(M^2\log M)$, independent of $n$ | production |

Circulant embedding wraps the covariance on a torus whose covariance matrix is block-circulant,
hence diagonalised by the 2-D DFT. One complex FFT of $\sqrt{\lambda}\,(\xi_1+i\xi_2)$ yields **two**
independent exact fields. The torus is padded by $1.5r$ instead of the classical $2N$: wrapped lags are
then $\ge 1.5r$ where $\rho\le e^{-4.5}\approx1\%$ — up to 4× less FFT work at a bounded, tested error
(FFT vs Cholesky correlation matrices agree to max 0.08, mean 0.02 on 6,000 realisations).

## 3. Vulnerability and secondary uncertainty

* **Mean damage ratio**: $\mathrm{MDR}_k(x)=\Phi\!\big(\ln(x/\theta_k)/\beta_k\big)$ by construction class,
  $\theta$ scaled by a year-built factor.
* **Damage given MDR**: $\mathrm{Beta}(\alpha,\beta)$ with mean $m$ and sd $k\sqrt{m(1-m)}$
  ($\alpha+\beta=1/k^2-1$). Coverage ratios: contents $=f_c\cdot$DR, BI $=\min(1,f_{bi}\cdot$DR$)$.
* **Fast inverse**: $F^{-1}_{\mathrm{Beta}}$ is tabulated on a (logit $m$) × $z$ grid and interpolated
  bilinearly — exact at nodes, monotone in both arguments, max error < 0.01 vs `betaincinv`,
  ~100× faster.

## 4. Damage correlation (pluggable `DamageCopula`)

Damage is $F^{-1}_{\mathrm{Beta}}(\Phi(z))$; the copula only shapes the joint law of $z$, so **every
marginal — and therefore AAL — is invariant** (tested). Tails are not.

| model | $z_i$ | correlation |
|---|---|---|
| `independent` | $\xi_i$ | 0 |
| `one_factor` | $\sqrt{\rho_i}Z+\sqrt{1-\rho_i}\xi_i$ | $\sqrt{\rho_i\rho_j}$, $\rho$ by construction class |
| `spatial` | $\sqrt{\rho}G(x_i)+\sqrt{1-\rho}\xi_i$ | $\rho e^{-3h_{ij}/r}$ (distance-decaying) |

**Closed-form check (generalised Vasicek).** For a homogeneous portfolio as $N\to\infty$, the loss
ratio given the factor is $g(Z)=\mathbb{E}_\xi\big[F^{-1}(\Phi(\sqrt\rho Z+\sqrt{1-\rho}\xi))\big]$ and,
$g$ being increasing, its $q$-quantile is $g(\Phi^{-1}(q))$. The test evaluates $g$ by 80-node
Gauss–Hermite quadrature and matches simulated quantiles at $q\in\{0.5,0.9,0.99\}$ within 3.5%.

**Benchmark** (`catmod benchmark`, 20k locations, quick preset, ρ=0.3, gross):

| copula | AAL | OEP 250 | TVaR 99 |
|---|---|---|---|
| independent | 32.7M | 899M | 896M |
| one_factor | 33.8M | 954M | 947M |
| spatial (10 km) | 32.5M | 903M | 867M |

AAL agrees within Monte Carlo error; the one-factor copula fattens the tail ≈ 6%. The spatial copula
moves it less here because hubs are hundreds of km apart — the dependence assumption is a
portfolio-specific modelling choice, which is why it is a first-class, swappable component.

## 5. Financial module

1. **Location** (OED `LocDed6All`/`LocDedType6All`/`LocLimit6All`): deductible as amount, % loss or
   % TIV; limit (0 = unlimited).
2. **Account layer**: $\min(\max(\sum \text{net}-\text{PolDed}-\text{attach},0),\text{limit})\times\text{participation}$,
   back-allocated pro rata to locations (for location AAL, segment EP and drill-down).
3. **Reinsurance** (in the YLT, since aggregate terms are annual): quota share, then per-occurrence
   XoL layers stacked on the QS-retained loss, each capped at limit × (1 + reinstatements) per year.

## 6. Simulation and metrics

* **ELT**: every loss-causing event × $S$ samples (seed = (run seed, event id) → any event recomputes
  bit-for-bit for drill-down; results are independent of worker count — tested).
* **YLT**: $N_{year}\sim\mathrm{Poisson}(\Lambda)$, event $\propto$ rate, uniform sample index.
* **Metrics**: AAL, OEP/AEP (empirical, $\lceil N/T\rceil$-th largest), VaR/TVaR; **standard errors**
  by 20 non-overlapping batch means; metrics whose return period exceeds the batch length report no
  SE rather than a misleading one.
* **Exact location AAL** $=\sum_e \lambda_e\,\overline{L}_{e,i}$ per peril (no simulation noise) drives
  maps and breakdowns; YLT AAL converges to it (tested within 4 SE).
* **Segment EP**: event-sample losses per (hub × occupancy × construction) let EP curves be
  recomputed for any filter combination — exactly additive across segments (tested); net is not
  separable (reinsurance is non-linear) and the API says so.

## 7. Live nowcasting

* **USGS** point sources → ensemble over 4 rupture strikes (fault plane unknown at first report).
* **NHC** persistence track (12 h back, 72 h forward) with landfall detection and decay.
* Each live event is run against the watched portfolios with full secondary uncertainty
  (≈200 ms for 30k locations); alert rules (mean / P90 / P99 gross or GU, locations hit) are
  evaluated and de-duplicated per (rule, event, portfolio), pushed over WebSocket.

## Known limitations

* Synthetic locations ignore coastlines (some points fall offshore); no land mask yet.
* TC model has no storm surge, rainfall, or re-intensification after crossing Florida.
* EQ uses PGA only (no spectral acceleration), linear site term, no basin effects.
* Vulnerability curves are generic per construction class; no secondary modifiers beyond age.
