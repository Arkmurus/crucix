"""Debug fly secrets parser."""
import subprocess

r = subprocess.run(
    ["flyctl", "secrets", "list", "-a", "aria-intel"],
    capture_output=True, timeout=30,
)
text = r.stdout.decode("utf-8", errors="replace")

for line in text.split("\n"):
    if "STATE" in line or "OLLAMA" in line:
        print("Line:", repr(line))
        if "\u2502" in line:
            name = line.split("\u2502")[0].strip()
            print("  Name:", repr(name))
        else:
            print("  No pipe found")
            print("  Hex:", [hex(ord(c)) for c in line[:20]])
