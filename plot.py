#!/usr/bin/env python3
"""NSS plotting script.

Reads every *_summary.csv in data/ (shared format:
source,distance_m,obstacle,rssi_dbm,loss_pct,rtt_ms) and saves:
  rssi_vs_distance.png  line-of-sight RSSI by distance, one line per source
  obstacle_rssi.png     RSSI by obstacle at each distance (real data)

Usage:
  python3 plot.py            # reads data/
  python3 plot.py --sample   # makes fake data first, to test the script
"""
import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

LABELS = {
    "real": "ESP32 (real)",
    "reference": "Laptop AP (reference)",
    "sim_friis": "ns-3 Friis",
    "sim_logdistance": "ns-3 Log-Distance",
}


def make_sample(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    rows = []
    for d, real, ref, friis, logd in [
        (1, -38, -37, -40, -41), (3, -48, -46, -50, -53),
        (5, -53, -51, -54, -59), (10, -61, -60, -60, -68), (15, -67, -65, -64, -73),
    ]:
        rows += [
            ("real", d, "none", real, 0.2 * d, 4 + 0.3 * d),
            ("reference", d, "none", ref, "", ""),
            ("sim_friis", d, "none", friis, 0.0, ""),
            ("sim_logdistance", d, "none", logd, 0.0, ""),
            ("real", d, "wall", real - 9, 0.5 * d, 5 + 0.4 * d),
        ]
    rows = [r + ("",) for r in rows]
    rows += [("direct", 12, "weakspot", -78, 6.5, 18.2, 4.1),
             ("via_esp32", 12, "weakspot", -55, 0.8, 24.6, 2.3)]
    pd.DataFrame(rows, columns=["source", "distance_m", "obstacle", "rssi_dbm", "loss_pct", "rtt_ms", "throughput_mbps"]) \
        .to_csv(os.path.join(data_dir, "sample_summary.csv"), index=False)
    print("Wrote sample data to", os.path.join(data_dir, "sample_summary.csv"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data")
    p.add_argument("--sample", action="store_true")
    args = p.parse_args()

    if args.sample:
        make_sample(args.data_dir)

    files = glob.glob(os.path.join(args.data_dir, "*_summary.csv"))
    if not files:
        print("No *_summary.csv files found in", args.data_dir)
        return
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

    # 1. Line of sight: RSSI vs distance, one line per source
    los = df[df["obstacle"] == "none"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for src, g in los.groupby("source"):
        g = g.groupby("distance_m", as_index=False)["rssi_dbm"].mean().sort_values("distance_m")
        ax.plot(g["distance_m"], g["rssi_dbm"], marker="o", label=LABELS.get(src, src))
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_title("Signal strength vs distance, line of sight")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig("rssi_vs_distance.png", dpi=150)
    print("Saved rssi_vs_distance.png")

    # 2. Real data: RSSI by obstacle at each distance
    real = df[df["source"] == "real"]
    if real["obstacle"].nunique() > 1:
        pivot = real.pivot_table(index="distance_m", columns="obstacle", values="rssi_dbm", aggfunc="mean")
        ax = pivot.plot(kind="bar", figsize=(7, 4.5))
        ax.set_xlabel("Distance (m)")
        ax.set_ylabel("RSSI (dBm)")
        ax.set_title("ESP32 signal strength by obstacle")
        ax.grid(axis="y", alpha=0.3)
        plt.tight_layout()
        plt.savefig("obstacle_rssi.png", dpi=150)
        print("Saved obstacle_rssi.png")

    # 3. SO3: direct vs. through the ESP32 repeater at the weak spot
    so3 = df[df["source"].isin(["direct", "via_esp32"])]
    if so3["source"].nunique() == 2:
        means = so3.groupby("source")[["rssi_dbm", "loss_pct", "rtt_ms", "throughput_mbps"]].mean()
        means = means.reindex(["direct", "via_esp32"])
        titles = [("rssi_dbm", "RSSI (dBm)"), ("loss_pct", "Packet loss (%)"),
                  ("rtt_ms", "Latency (ms)"), ("throughput_mbps", "Throughput (Mbps)")]
        fig, axes = plt.subplots(1, 4, figsize=(12, 3.6))
        for ax, (col, label) in zip(axes, titles):
            ax.bar(["Direct", "Via ESP32"], means[col], color=["#888888", "#3b6fd4"])
            ax.set_title(label)
            ax.grid(axis="y", alpha=0.3)
        fig.suptitle("Weak spot: direct vs. through the ESP32 repeater")
        fig.tight_layout()
        fig.savefig("so3_comparison.png", dpi=150)
        print("Saved so3_comparison.png")


if __name__ == "__main__":
    main()
