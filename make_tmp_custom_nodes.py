import json
import re
from pathlib import Path


def fetch_repo_url(node_dir: Path) -> str:
    """
    Reads the first line of .git/FETCH_HEAD and tries to extract the remote URL.
    Returns the full line (stripped) if no URL is found.
    """
    fetch_head = node_dir / ".git" / "FETCH_HEAD"
    if not fetch_head.is_file():
        return ""

    first_line = fetch_head.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not first_line:
        return ""

    line = first_line[0]
    match = re.search(r"https?://\S+", line)
    return match.group(0) if match else line.strip()


def collect_custom_nodes(custom_nodes_dir: Path) -> dict[str, dict[str, str]]:
    data = {"loaded": {}, "disabled": {}}

    disabled_dir = custom_nodes_dir / ".disabled"
    if disabled_dir.is_dir():
        for item in sorted(disabled_dir.iterdir()):
            if item.is_dir():
                data["disabled"][item.name] = fetch_repo_url(item)

    for item in sorted(custom_nodes_dir.iterdir()):
        if item.name == ".disabled" or not item.is_dir():
            continue
        data["loaded"][item.name] = fetch_repo_url(item)

    return data


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    custom_nodes_dir = script_dir.parents[1] / "custom_nodes"
    if not custom_nodes_dir.is_dir():
        raise SystemExit(f"custom_nodes directory not found at: {custom_nodes_dir}")

    data = collect_custom_nodes(custom_nodes_dir)
    output_path = script_dir / "tmp_custom_nodes.json"
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
