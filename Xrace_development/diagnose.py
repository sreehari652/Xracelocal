import socket
import json
import time

print("=" * 60)
print("UWB DIAGNOSTICS TOOL")
print("=" * 60)

PORT = 4210
TIMEOUT = 10

# -------------------------------------------------
# Create UDP socket
# -------------------------------------------------
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', PORT))
    sock.settimeout(TIMEOUT)
    print(f"\n✓ Successfully bound to UDP port {PORT}")
except Exception as e:
    print(f"\n✗ Failed to bind to port {PORT}: {e}")
    exit(1)

print("\nIMPORTANT:")
print("1️⃣ Power OFF all ESP32 / UWB devices first.")
print("2️⃣ Then wait and observe if data still arrives.")
print("-" * 60)

# -------------------------------------------------
# Test 1 – Single Packet Test
# -------------------------------------------------
print("\n[Test 1] Waiting for ONE packet...")

try:
    data, addr = sock.recvfrom(2048)

    print("\n✓ DATA RECEIVED!")
    print("FROM:", addr)
    print("RAW:", data)

    try:
        decoded = data.decode("utf-8")
        print("\nDecoded JSON:")
        print(decoded)

        parsed = json.loads(decoded)
        print("\n✓ Valid JSON Structure:")
        print(json.dumps(parsed, indent=2))

        if "timestamp" in parsed:
            if parsed["timestamp"] == 0:
                print("\n⚠ WARNING: Timestamp is 0 → Likely SIMULATION data")
            else:
                print("\n✓ Real timestamp detected")

    except Exception as e:
        print("\n✗ Data is not valid JSON:", e)

except socket.timeout:
    print("\n✓ No data received in 10 seconds (GOOD if devices are OFF)")

# -------------------------------------------------
# Test 2 – Continuous Monitor
# -------------------------------------------------
print("\n" + "-" * 60)
print("[Test 2] Continuous Monitoring (Press Ctrl+C to stop)")
print("-" * 60)

sock.settimeout(None)  # Blocking mode

try:
    while True:
        data, addr = sock.recvfrom(2048)
        timestamp = time.strftime("%H:%M:%S")

        print(f"\n[{timestamp}] Packet from {addr}")
        print("Data:", data.decode("utf-8"))

except KeyboardInterrupt:
    print("\n\nMonitoring stopped by user.")

sock.close()

print("\n" + "=" * 60)
print("Diagnostics Complete")
print("=" * 60)
