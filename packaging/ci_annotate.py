"""
CI helper: print a failed step's output as GitHub error annotations, which anyone can read
(job logs need admin rights). Each annotation holds at most ~4 KB, so the output is split.

    python packaging/ci_annotate.py TITLE FILE [FILE ...]

From unittest output only the failure details are kept (from the first ===== line on); otherwise,
e.g. after a crash, the last lines.
"""
import sys

CHUNK = 3500
MAX_ANNOTATIONS = 8


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main():
    title, paths = sys.argv[1], sys.argv[2:]
    text = ""
    for path in paths:
        try:
            with open(path, errors="replace") as f:
                text += f.read()
        except OSError as e:
            text += f"({path}: {e})\n"
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("======")), None)
    lines = lines[start:] if start is not None else lines[-80:]
    text = "\n".join(lines)
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] or ["(no output)"]
    if len(chunks) > MAX_ANNOTATIONS:
        chunks = chunks[:MAX_ANNOTATIONS - 1] + [chunks[-1]]
    for n, chunk in enumerate(chunks, 1):
        print(f"::error title={title} ({n}/{len(chunks)})::{escape(chunk)}")


if __name__ == "__main__":
    main()
