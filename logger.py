#!/usr/bin/env python3
"""NSS laptop logger.

Receives packets from the ESP32 (format: seq,rssi_dbm,last_rtt_ms), echoes
each seq back so the ESP32 can time the round trip, and writes:
  data/real_raw.csv      one row per packet
  data/real_summary.csv  one row per test, in the shared format

Usage:
  python3 logger.py --distance 5 --obstacle none --seconds 30 --iface wlp1s0

With --iface, the laptop also reads the ESP32's signal as heard by the
hotspot (iw station dump) and writes it to data/reference_summary.csv.
"""
import argparse
import csv
import os
import socket
import re
import statistics
import subprocess
import time

RAW_FIELDS = ["test_id", "distance_m", "obstacle", "recv_time", "seq", "rssi_dbm", "rtt_ms", "ap_rssi_dbm"]
SUMMARY_FIELDS = ["source", "distance_m", "obstacle", "rssi_dbm", "loss_pct", "rtt_ms", "throughput_mbps"]


def parse_station_dump(text):
    """Return the signal (dBm) of the first station in `iw dev <iface> station dump` output."""
    m = re.search(r"^\s*signal:\s*(-?\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def read_ap_rssi(iface):
    """RSSI of the ESP32 as measured by the laptop (the access point). Reference reading."""
    if not iface:
        return None
    try:
        out = subprocess.run(["iw", "dev", iface, "station", "dump"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_station_dump(out)


def append_rows(path, fields, rows):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--distance", type=float, required=True, help="distance in meters")
    p.add_argument("--obstacle", default="none", choices=["none", "wall", "door", "floor"])
    p.add_argument("--seconds", type=int, default=30, help="test duration")
    p.add_argument("--port", type=int, default=5005)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--iface", default="", help="hotspot Wi-Fi interface (e.g. wlp1s0) for laptop-side reference RSSI")
    args = p.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    test_id = time.strftime("%Y%m%d-%H%M%S")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0)

    print(f"Test {test_id}: {args.distance} m, obstacle={args.obstacle}, {args.seconds}s")
    print("Waiting for ESP32 packets...")

    rows = []
    start = None
    ap_rssi, last_ap_read = None, 0.0
    while True:
        if start and time.time() - start >= args.seconds:
            break
        try:
            data, addr = sock.recvfrom(64)
        except socket.timeout:
            continue
        try:
            seq_s, rssi_s, rtt_s = data.decode().strip().split(",")
            seq, rssi, rtt = int(seq_s), int(rssi_s), float(rtt_s)
        except ValueError:
            continue
        sock.sendto(seq_s.encode(), addr)  # echo for RTT
        if start is None:
            start = time.time()
        if time.time() - last_ap_read >= 1.0:  # read the laptop-side RSSI once per second
            ap_rssi, last_ap_read = read_ap_rssi(args.iface), time.time()
        rows.append({
            "test_id": test_id,
            "distance_m": args.distance,
            "obstacle": args.obstacle,
            "recv_time": round(time.time(), 4),
            "seq": seq,
            "rssi_dbm": rssi,
            "rtt_ms": rtt if rtt >= 0 else "",
            "ap_rssi_dbm": ap_rssi if ap_rssi is not None else "",
        })
        if len(rows) % 10 == 0:
            print(f"  seq={seq:6d}  rssi={rssi:4d} dBm  rtt={rtt:7.2f} ms  received={len(rows)}")

    sock.close()
    if not rows:
        print("No packets received. Check the hotspot, LOGGER_IP in the sketch, and the firewall port.")
        return

    seqs = [r["seq"] for r in rows]
    expected = max(seqs) - min(seqs) + 1
    received = len(set(seqs))
    loss_pct = round(100 * (expected - received) / expected, 2)
    rssi_mean = round(statistics.mean(r["rssi_dbm"] for r in rows), 2)
    rtts = [r["rtt_ms"] for r in rows if r["rtt_ms"] != ""]
    rtt_mean = round(statistics.mean(rtts), 2) if rtts else ""

    append_rows(os.path.join(args.data_dir, "real_raw.csv"), RAW_FIELDS, rows)
    append_rows(os.path.join(args.data_dir, "real_summary.csv"), SUMMARY_FIELDS, [{
        "source": "real",
        "distance_m": args.distance,
        "obstacle": args.obstacle,
        "rssi_dbm": rssi_mean,
        "loss_pct": loss_pct,
        "rtt_ms": rtt_mean,
        "throughput_mbps": "",
    }])
    ap_vals = [r["ap_rssi_dbm"] for r in rows if r["ap_rssi_dbm"] != ""]
    if ap_vals:
        append_rows(os.path.join(args.data_dir, "reference_summary.csv"), SUMMARY_FIELDS, [{
            "source": "reference",
            "distance_m": args.distance,
            "obstacle": args.obstacle,
            "rssi_dbm": round(statistics.mean(ap_vals), 2),
            "loss_pct": "", "rtt_ms": "", "throughput_mbps": "",
        }])

    print(f"\nDone. received={received}/{expected}  loss={loss_pct}%  "
          f"mean RSSI={rssi_mean} dBm  mean RTT={rtt_mean} ms"
          + (f"  laptop-side RSSI={round(statistics.mean(ap_vals), 2)} dBm" if ap_vals else ""))


if __name__ == "__main__":
    main()
