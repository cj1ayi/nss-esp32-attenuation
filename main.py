# NSS ESP32 measurement node (MicroPython version)
# Same behavior as nss_node.ino: sends "seq,rssi_dbm,last_rtt_ms" over UDP
# every INTERVAL_MS to the laptop logger, which echoes seq back for RTT.
# Works with the same logger.py, no changes needed.

import network
import socket
import time

# ---- Edit these ----
WIFI_SSID = "NSS-Test"
WIFI_PASS = "testpass123"
LOGGER_IP = "10.42.0.1"  # default IP of a NetworkManager hotspot on Fedora
LOGGER_PORT = 5005
INTERVAL_MS = 100
# --------------------


def connect(wlan):
    wlan.active(True)
    try:
        wlan.config(pm=wlan.PM_NONE)  # keep the radio awake so RTT is not skewed by power saving
    except Exception:
        pass
    wlan.connect(WIFI_SSID, WIFI_PASS)
    print("Connecting", end="")
    while not wlan.isconnected():
        time.sleep_ms(300)
        print(".", end="")
    print("\nConnected. IP:", wlan.ifconfig()[0], " RSSI:", wlan.status("rssi"), "dBm")


wlan = network.WLAN(network.STA_IF)
connect(wlan)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", LOGGER_PORT))
sock.setblocking(False)
dest = socket.getaddrinfo(LOGGER_IP, LOGGER_PORT)[0][-1]

seq = 0
pending_seq = 0
sent_at_us = 0
last_rtt_ms = -1.0
last_send = time.ticks_ms()

while True:
    if not wlan.isconnected():
        print("Wi-Fi lost, reconnecting...")
        connect(wlan)

    # Check for an echo from the logger
    try:
        data = sock.recv(32)
        if int(data) == pending_seq:
            last_rtt_ms = time.ticks_diff(time.ticks_us(), sent_at_us) / 1000
    except (OSError, ValueError):
        pass

    # Send the next packet
    if time.ticks_diff(time.ticks_ms(), last_send) >= INTERVAL_MS:
        last_send = time.ticks_ms()
        seq += 1
        rssi = wlan.status("rssi")
        msg = "{},{},{:.2f}".format(seq, rssi, last_rtt_ms)
        try:
            sock.sendto(msg.encode(), dest)
        except OSError:
            pass
        pending_seq = seq
        sent_at_us = time.ticks_us()
        last_rtt_ms = -1.0  # reset until the echo for this seq arrives

        if seq % 10 == 0:
            print("seq={} rssi={} dBm".format(seq, rssi))

    time.sleep_ms(2)
