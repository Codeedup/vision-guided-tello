import argparse
import json
import socket
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    args = parser.parse_args()

    last_report_time = 0.0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind((args.bind, 5005))
        print(f"Listening on {args.bind}:5005. Ctrl+C to stop.")

        try:
            while True:
                packet, address = receiver.recvfrom(4096)
                received_ns = time.time_ns()

                try:
                    observation = json.loads(packet.decode("utf-8"))
                    capture_ns = observation["capture_time_ns"]

                    if type(capture_ns) is not int or capture_ns <= 0:
                        raise ValueError("Invalid capture timestamp")

                except (UnicodeError, ValueError, KeyError, TypeError):
                    print("Skipped malformed observation.")
                    continue

                age_ms = (received_ns - capture_ns) / 1_000_000
                report_time = time.monotonic()

                if report_time - last_report_time >= 0.5:
                    print(
                        f"age_ms={age_ms:.1f} "
                        f"{json.dumps(observation)}"
                    )
                    last_report_time = report_time

        except KeyboardInterrupt:
            print("\nReceiver stopped.")


if __name__ == "__main__":
    main()