#!/usr/bin/env python3
"""NSS plotting + analysis script.

Reads every *_summary.csv in data/ (shared format:
source,distance_m,obstacle,rssi_dbm,loss_pct,rtt_ms,throughput_mbps) and saves:

  rssi_vs_distance.png      LOS RSSI by distance, one line per source (error bars = std of repeats)
  path_loss_fit.png         RSSI vs log10(distance) with fitted path-loss exponent per source   (SO4)
  obstacle_rssi.png         ESP32 RSSI by obstacle at each distance
  obstacle_attenuation.png  extra loss (dB) caused by each obstacle vs. line of sight
  esp32_vs_reference.png    ESP32-side RSSI vs laptop-side RSSI, with 1:1 line                  (SO1)
  loss_rtt_vs_distance.png  packet loss and RTT vs distance (real, line of sight)
  so3_comparison.png        weak spot: direct vs. through the ESP32 repeater                    (SO3)

and writes two tables you can paste straight into the analysis section:
  analysis_metrics.csv      MAE / RMSE / bias between sources (sim vs real, reference vs real)  (SO4/SO5)
  path_loss_fit.csv         fitted path-loss exponent n and RSSI at 1 m per source

Usage:
  python3 plot.py                       # reads data/
  python3 plot.py --sample              # fake data in data_sample/ (never touches data/)
  python3 plot.py --out-dir figures     # save PNG/CSV outputs somewhere else
"""
import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

LABELS = {
    "real": "ESP32 (real)",
    "reference": "Laptop AP (reference)",
    "sim_friis": "ns-3 Friis",
    "sim_logdistance": "ns-3 Log-Distance",
}
VALUE_COLS = ["rssi_dbm", "loss_pct", "rtt_ms", "throughput_mbps"]


def label(src):
    return LABELS.get(src, src)


def make_sample(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    rows = []
    for d, real, ref, friis, logd in [
        (1, -38, -37, -40, -41), (3, -48, -46, -50, -53),
        (5, -53, -51, -54, -59), (10, -61, -60, -60, -68), (15, -67, -65, -64, -73),
    ]:
        rows += [
            ("real", d, "none", real, 0.2 * d, 4 + 0.3 * d),
            ("real", d, "none", real + 1, 0.2 * d, 4 + 0.3 * d),  # a repeat run, to test error bars
            ("reference", d, "none", ref, "", ""),
            ("sim_friis", d, "none", friis, 0.0, ""),
            ("sim_logdistance", d, "none", logd, 0.0, ""),
            ("real", d, "wall", real - 9, 0.5 * d, 5 + 0.4 * d),
            ("reference", d, "wall", ref - 8, "", ""),
        ]
    rows = [r + ("",) for r in rows]
    rows += [("direct", 12, "weakspot", -78, 6.5, 18.2, 4.1),
             ("via_esp32", 12, "weakspot", -55, 0.8, 24.6, 2.3)]
    pd.DataFrame(rows, columns=["source", "distance_m", "obstacle"] + VALUE_COLS) \
        .to_csv(os.path.join(data_dir, "sample_summary.csv"), index=False)
    print("Wrote sample data to", os.path.join(data_dir, "sample_summary.csv"))


def load(data_dir):
    files = sorted(glob.glob(os.path.join(data_dir, "*_summary.csv")))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    for c in VALUE_COLS:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def los_stats(df, src, col="rssi_dbm"):
    """Per-distance mean/std/count of `col` for one source, line of sight only."""
    g = df[(df["source"] == src) & (df["obstacle"] == "none")].dropna(subset=[col])
    if g.empty:
        return pd.DataFrame(columns=["distance_m", "mean", "std", "count"])
    out = g.groupby("distance_m")[col].agg(["mean", "std", "count"]).reset_index()
    out["std"] = out["std"].fillna(0)
    return out.sort_values("distance_m")


def fit_path_loss(stats):
    """Fit RSSI = P1 - 10*n*log10(d). Returns (n, P1, r2) or None if not fittable."""
    if stats["distance_m"].nunique() < 2:
        return None
    x = 10 * np.log10(stats["distance_m"].to_numpy(float))
    y = stats["mean"].to_numpy(float)
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return -slope, intercept, r2


def compare(a, b):
    """Error of series a relative to series b at the distances both have (a - b)."""
    m = a.merge(b, on="distance_m", suffixes=("_a", "_b"))
    if m.empty:
        return None
    diff = m["mean_a"] - m["mean_b"]
    return {"n_points": len(m), "mae_db": diff.abs().mean(),
            "rmse_db": float(np.sqrt((diff ** 2).mean())), "bias_db": diff.mean()}


def save(fig, out_dir, name):
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, name), dpi=150)
    plt.close(fig)
    print("Saved", name)


# ---------------------------------------------------------------- plots

def plot_rssi_vs_distance(df, out_dir, sources):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    plotted = False
    for src in sources:
        s = los_stats(df, src)
        if s.empty:
            continue
        ax.errorbar(s["distance_m"], s["mean"], yerr=s["std"], marker="o", capsize=3, label=label(src))
        plotted = True
    if not plotted:
        plt.close(fig)
        return
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("Signal strength vs distance, line of sight")
    ax.grid(alpha=0.3)
    ax.legend()
    save(fig, out_dir, "rssi_vs_distance.png")


def plot_path_loss_fit(df, out_dir, sources):
    """Fit n per source, plot on a log-distance axis, return the fit table."""
    fig, ax = plt.subplots(figsize=(7, 4.5))
    fits = []
    for i, src in enumerate(sources):
        s = los_stats(df, src)
        if s.empty:
            continue
        color = f"C{i}"
        ax.scatter(s["distance_m"], s["mean"], color=color, zorder=3)
        f = fit_path_loss(s)
        if f is None:
            continue
        n, p1, r2 = f
        dd = np.linspace(s["distance_m"].min(), s["distance_m"].max(), 100)
        ax.plot(dd, p1 - 10 * n * np.log10(dd), "--", color=color,
                label=f"{label(src)}: n={n:.2f}, R²={r2:.2f}")
        fits.append({"source": src, "path_loss_exponent_n": round(n, 3),
                     "rssi_at_1m_dbm": round(p1, 2), "r2": round(r2, 3), "n_distances": len(s)})
    if not fits:
        plt.close(fig)
        return None
    ax.set_xscale("log")
    ax.set_xticks(sorted(df["distance_m"].dropna().unique()))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.set_xlabel("Distance (m, log scale)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("Path-loss fit: RSSI = P(1 m) − 10·n·log10(d)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    save(fig, out_dir, "path_loss_fit.png")
    return pd.DataFrame(fits)


def plot_obstacles(df, out_dir):
    real = df[df["source"] == "real"].dropna(subset=["rssi_dbm"])
    if real["obstacle"].nunique() < 2:
        return
    pivot = real.pivot_table(index="distance_m", columns="obstacle", values="rssi_dbm", aggfunc="mean")
    ax = pivot.plot(kind="bar", figsize=(7, 4.5))
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("ESP32 signal strength by obstacle")
    ax.grid(axis="y", alpha=0.3)
    save(ax.figure, out_dir, "obstacle_rssi.png")

    # extra attenuation vs. line of sight at the same distance (positive = weaker than LOS)
    if "none" not in pivot.columns:
        return
    att = pd.DataFrame({o: pivot["none"] - pivot[o] for o in pivot.columns if o != "none"}).dropna(how="all")
    if att.empty:
        return
    ax = att.plot(kind="bar", figsize=(7, 4.5))
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Extra attenuation vs. line of sight (dB)")
    ax.set_title("Obstacle attenuation (ESP32, real)")
    ax.grid(axis="y", alpha=0.3)
    ax.axhline(0, color="black", linewidth=0.8)
    save(ax.figure, out_dir, "obstacle_attenuation.png")
    avg = att.mean().round(2)
    print("  Mean extra attenuation (dB):", ", ".join(f"{k}={v}" for k, v in avg.items()))


def plot_esp32_vs_reference(df, out_dir):
    def means(src):
        g = df[df["source"] == src].dropna(subset=["rssi_dbm"])
        return g.groupby(["distance_m", "obstacle"], as_index=False)["rssi_dbm"].mean()

    m = means("real").merge(means("reference"), on=["distance_m", "obstacle"], suffixes=("_esp", "_ref"))
    if m.empty:
        return None
    diff = m["rssi_dbm_esp"] - m["rssi_dbm_ref"]
    r = np.corrcoef(m["rssi_dbm_esp"], m["rssi_dbm_ref"])[0, 1] if len(m) > 1 else float("nan")
    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    ax.scatter(m["rssi_dbm_ref"], m["rssi_dbm_esp"], zorder=3)
    lo = min(m["rssi_dbm_ref"].min(), m["rssi_dbm_esp"].min()) - 3
    hi = max(m["rssi_dbm_ref"].max(), m["rssi_dbm_esp"].max()) + 3
    ax.plot([lo, hi], [lo, hi], "k--", linewidth=1, label="1:1 (perfect agreement)")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Laptop-side RSSI (dBm)")
    ax.set_ylabel("ESP32-side RSSI (dBm)")
    ax.set_title(f"ESP32 vs. laptop reference\nbias={diff.mean():.2f} dB, MAE={diff.abs().mean():.2f} dB, r={r:.3f}")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    save(fig, out_dir, "esp32_vs_reference.png")
    return {"n_points": len(m), "mae_db": diff.abs().mean(),
            "rmse_db": float(np.sqrt((diff ** 2).mean())), "bias_db": diff.mean()}


def plot_loss_rtt(df, out_dir):
    loss, rtt = los_stats(df, "real", "loss_pct"), los_stats(df, "real", "rtt_ms")
    if loss.empty and rtt.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, s, title, unit in [(axes[0], loss, "Packet loss vs distance", "Packet loss (%)"),
                               (axes[1], rtt, "Round-trip time vs distance", "RTT (ms)")]:
        if not s.empty:
            ax.errorbar(s["distance_m"], s["mean"], yerr=s["std"], marker="o", capsize=3, color="#3b6fd4")
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel(unit)
        ax.set_title(title)
        ax.grid(alpha=0.3)
    save(fig, out_dir, "loss_rtt_vs_distance.png")


def plot_so3(df, out_dir):
    so3 = df[df["source"].isin(["direct", "via_esp32"])]
    if so3["source"].nunique() != 2:
        return
    means = so3.groupby("source")[VALUE_COLS].mean().reindex(["direct", "via_esp32"])
    titles = [("rssi_dbm", "RSSI (dBm)"), ("loss_pct", "Packet loss (%)"),
              ("rtt_ms", "Latency (ms)"), ("throughput_mbps", "Throughput (Mbps)")]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.6))
    for ax, (col, lab) in zip(axes, titles):
        ax.bar(["Direct", "Via ESP32"], means[col], color=["#888888", "#3b6fd4"])
        ax.set_title(lab)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("Weak spot: direct vs. through the ESP32 repeater")
    save(fig, out_dir, "so3_comparison.png")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default=None, help="default: data (or data_sample with --sample)")
    p.add_argument("--out-dir", default=".", help="where PNG/CSV outputs go")
    p.add_argument("--sample", action="store_true")
    args = p.parse_args()

    data_dir = args.data_dir or ("data_sample" if args.sample else "data")
    os.makedirs(args.out_dir, exist_ok=True)
    if args.sample:
        make_sample(data_dir)

    df = load(data_dir)
    if df is None:
        print("No *_summary.csv files found in", data_dir)
        return

    # plotting order: real, reference, then simulations
    present = list(df["source"].unique())
    sources = [s for s in ["real", "reference"] if s in present] + sorted(s for s in present if s.startswith("sim_"))

    plot_rssi_vs_distance(df, args.out_dir, sources)
    fit_df = plot_path_loss_fit(df, args.out_dir, sources)
    plot_obstacles(df, args.out_dir)
    sym = plot_esp32_vs_reference(df, args.out_dir)
    plot_loss_rtt(df, args.out_dir)
    plot_so3(df, args.out_dir)

    # ---- tables for the analysis section ----
    real_los = los_stats(df, "real")
    metrics = []
    if sym:
        metrics.append({"comparison": "ESP32 vs laptop reference (all matched spots)", **sym})
    for src in sources:
        if src.startswith("sim_"):
            c = compare(los_stats(df, src), real_los)
            if c:
                metrics.append({"comparison": f"{label(src)} vs ESP32 real (LOS, sim − real)", **c})
    if metrics:
        mdf = pd.DataFrame(metrics).round(2)
        mdf.to_csv(os.path.join(args.out_dir, "analysis_metrics.csv"), index=False)
        print("\nDiscrepancy metrics (saved to analysis_metrics.csv):")
        print(mdf.to_string(index=False))
    if fit_df is not None:
        fit_df.to_csv(os.path.join(args.out_dir, "path_loss_fit.csv"), index=False)
        print("\nPath-loss fits (saved to path_loss_fit.csv):")
        print(fit_df.to_string(index=False))


if __name__ == "__main__":
    main()