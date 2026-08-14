import sys


def parse_file(filepath):
    items = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # Skip blank lines and single-letter section headers (A, B, C, ...)
            if not line or (len(line) == 1 and line.isalpha()):
                continue

            # Split the line on commas, clean up each entry
            for entry in line.split(","):
                entry = entry.strip().rstrip(".").strip()
                if entry:
                    items.append(entry)

    return items


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "medicines.txt"
    remedies = parse_file(filepath)

    print(f"Parsed {len(remedies)} entries.\n")
    print(remedies)
