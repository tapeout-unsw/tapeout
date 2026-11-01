#!/usr/bin/env python3
"""Upload BOB OS and open its line-oriented serial console on Linux/macOS."""

import argparse
import os
from pathlib import Path
import select
import struct
import sys
import termios
import time
import tty


def write_all(fd, data):
    while data:
        written = os.write(fd, data)
        data = data[written:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", help="serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--image", type=Path, help="upload bin/os.bin after a board reset")
    args = parser.parse_args()
    image = args.image.read_bytes() if args.image else None
    if image is not None and (not image or len(image) % 4 or len(image) > 0x300):
        parser.error("OS image must contain 1..192 complete 32-bit words")

    fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY)
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        # setraw leaves stop bits and hardware flow control as they were.
        attrs[2] &= ~(termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        if image is not None:
            write_all(fd, struct.pack("<I", len(image) // 4) + image)
            termios.tcdrain(fd)
        print("115200 baud, 8N1. Enter hex words, ! to stop core1, Ctrl-C to leave.", flush=True)
        # Local terminal line editing provides echo and backspace. The OS does
        # not echo characters. Wait for its prompt before submitting input.
        ready = image is None
        received = b""
        deadline = time.monotonic() + 5
        while True:
            if not ready and time.monotonic() > deadline:
                raise TimeoutError("No OS prompt. Reset the board before uploading again.")
            readable, _, _ = select.select([fd, sys.stdin] if ready else [fd], [], [], 0.2)
            if fd in readable:
                data = os.read(fd, 4096)
                if not data:
                    raise OSError("Serial device disconnected")
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                received = (received + data)[-5:]
                if received.endswith(b"hex> "):
                    ready = True
            if sys.stdin in readable:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                data.decode("ascii")
                write_all(fd, data)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, previous)
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
    except (OSError, TimeoutError, UnicodeError) as exc:
        sys.exit(str(exc))
