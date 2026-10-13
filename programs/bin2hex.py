import argparse
from pathlib import Path


def convert(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    if len(data) % 4:
        raise ValueError(f"{source}: size must be a multiple of 4 bytes")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="ascii") as out:
        for offset in range(0, len(data), 4):
            word = int.from_bytes(data[offset:offset + 4], "little")
            out.write(f"{word:08x}\n")
            print(f"{source} -> {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help=".bin file or directory")
    parser.add_argument("destination", type=Path, help=".hex file or directory")
    args = parser.parse_args()

    if args.source.is_dir():
        for source in sorted(args.source.glob("*.bin")):
            convert(source, args.destination / source.with_suffix(".hex").name)
    else:
        convert(args.source, args.destination)


if __name__ == "__main__":
    main()
