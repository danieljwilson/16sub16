# Distribution Analysis: Predicted Finish Time Uncertainty

*16sub16 tracker · Feb 2026*

---

## 1. How σ is Computed

The shared forecast uncertainty (σ, shown in the ridge plot) comes from two sources combined
in quadrature:

```
σ(t) = √( post_sig² · horizon² + linFit.sigma² )
```

**Terms:**

| Symbol | Meaning |
|---|---|
| `post_sig` | Posterior standard deviation of the Bayesian slope estimate (s/day) |
| `horizon` | Days from today to the target date: `max(0, t − today)` |
| `linFit.sigma` | Residual standard error of the OLS linear regression through all 3 races |

The posterior slope uncertainty (`post_sig`) is computed via precision-weighted Bayesian
updating:

```
1/post_var = 1/prior_sig² + 1/data_sig²
post_sig   = √post_var
```

where `data_sig = linFit.sigma / √Sxx` (standard error of the OLS slope), and the prior is
`−1.5 s/day ± 1.2` (a trained endurance athlete new to running).

At the goal day (Apr 19, Day 112, horizon ≈ 62 days from mid-February), the combined
σ is roughly **75 seconds** — a substantial spread that reflects genuine uncertainty 62 days out.

---

## 2. What σ Represents (and What It's Missing)

**What σ captures:**

- Uncertainty in the underlying improvement trend (slope posterior uncertainty × time horizon)
- Measurement noise around the fitted trend line (regression residuals)

**What σ does not capture:**

- **Race-day jitter**: performance varies ±30–60 s depending on weather, pacing, sleep, and
  course conditions — none of which is in the training data.
- **Injury risk**: a calf strain tomorrow sets the model to zero; no probability mass is placed
  on catastrophic non-completion.
- **Structural breaks**: the model assumes the improvement trajectory continues smoothly.
  A plateau or a breakthrough (new shoe tech, altitude training) are not modelled.
- **Model misspecification**: all models assume smooth functional forms fitted to only 3 data
  points. The true uncertainty is larger than any single model implies.

In short, σ ≈ 75 s is probably an **underestimate** of total race-day uncertainty, even if it is
a reasonable representation of trend uncertainty alone.

---

## 3. Why a Symmetric Normal Is Inappropriate

Every model in the ridge plot uses a Gaussian centered on its Apr 19 prediction with the shared σ.
This is statistically convenient but physically wrong for two reasons:

### 3a. PB is an upper envelope, not a center

The race times we observe are **personal bests** (or near-bests): you only line up for a race
when rested and ready. The distribution of *actual outcomes given preparation* is therefore
**left-skewed** (tail toward slower times). The observed race times are drawn from the
*right tail* of what you'd run on a random day, not the center.

A symmetric Gaussian around the model's predicted PB trajectory places equal probability mass
on "5% faster than predicted" and "5% slower than predicted," but the first scenario requires
exceptional conditions while the second could arise from many ordinary setbacks. The true
conditional distribution should be skewed toward slower times.

### 3b. Times are bounded below by physiology

A Gaussian on time (seconds) assigns non-zero probability to times below any physiological
floor. The model predicts ~16:00, and a Gaussian with σ = 75 s gives ~2.3% probability of
running < 14:45 — an implausibly fast time given current fitness (current PB = 17:29).

---

## 4. The Lower-Tail Problem

With a symmetric Gaussian centered around 16:00–16:30 (typical model predictions at goal day)
and σ = 75 s, the model assigns substantial probability to running faster than 14:45 and — more
importantly — substantial probability to running *slower than current best*.

**Concrete example** (Banister prediction ~16:31, σ = 75 s):

```
P(< 16:00) ≈ 35%
P(> 17:29) = P(> current PB + 58s) ≈ 22%
```

A 22% probability of running *worse than your current personal best* in a peak race
is counterintuitive and inconsistent with how race-day performance works: a well-tapered
athlete typically performs at or near their best, not randomly worse than training-log level.

The symmetric Gaussian fails to respect the asymmetry between "better than PB day"
(requires specific conditions) and "at or near PB level" (the default in a target race).

---

## 5. Fix Options (Ranked by Complexity)

### Option A: Truncated Normal at PB + buffer *(simplest; recommended)*

Truncate the Gaussian at the current PB + 15 s (e.g., 17:44 = 1064 s). This clips the
implausible right tail while preserving the rest of the distribution shape. All probability
mass at times > 1064 s is removed and the distribution renormalized.

**Pros**: one-line change to the PDF rendering; maintains the current uncertainty model.
**Cons**: truncation point is somewhat arbitrary; the renormalized distribution will have a
sharp discontinuity at the truncation point.

### Option B: Log-normal on finishing time

Model `log(time)` as Gaussian rather than `time` itself. Log-normal distributions are strictly
positive, right-skewed (appropriate for performance), and have a natural lower bound.

**Pros**: theoretically motivated; no arbitrary truncation.
**Cons**: requires reparameterising the entire uncertainty model; σ interpretation changes.

### Option C: Asymmetric (split-normal / skew-normal) distribution

Use a skew-normal or split-normal centered on the prediction, with σ_left < σ_right to
reflect that overperformance tails are thinner than underperformance tails.

**Pros**: captures the physical asymmetry explicitly.
**Cons**: introduces an additional free parameter (skewness) that would need to be set by
prior judgment rather than data (only 3 race results).

### Option D: Ensemble of scenarios

Instead of a single parametric distribution, enumerate discrete scenarios (e.g., "plateau",
"steady progress", "breakthrough") with assigned probability weights and show the resulting
mixture distribution.

**Pros**: most transparent and interpretable.
**Cons**: high implementation complexity; scenario weights are subjective.

---

## 6. Recommendation

**Implement Option A (truncated normal at PB + 15 s) as the first fix.**

This is a minimal, reversible change that removes the most counterintuitive portion of the
distribution (predicting worse than current best) without requiring a reparameterisation
of the uncertainty model. After more races are run and σ naturally shrinks, the truncation
will have decreasing impact.

The truncation point of `PB + 15 s` reflects:
- Current PB = 1049 s (17:29, Monaco, Day 49)
- 15 s buffer = roughly one missed split or a minor adverse condition

**Deferred to a future change.** This report documents the analysis so the fix can be
implemented cleanly once the distribution rendering is refactored.

---

*Generated: Feb 2026. Re-evaluate after ≥2 more race results when the OLS uncertainty σ shrinks.*
