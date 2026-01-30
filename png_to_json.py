import json
import os
import subprocess
from typing import Any, Dict, Optional

from PIL import Image, UnidentifiedImageError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".gif"}


def _write_json_next_to(path: str, workflow_dict: Dict[str, Any]) -> None:
    json_file_path = os.path.splitext(path)[0] + ".json"
    with open(json_file_path, "w", encoding="utf-8") as json_file:
        json.dump(workflow_dict, json_file, ensure_ascii=False, indent=4)
    print(f"--> {path}")


def _extract_workflow_from_text(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", "ignore")
    else:
        text = str(value)
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_workflow_from_image(image_path: str) -> Optional[Dict[str, Any]]:
    try:
        img = Image.open(image_path)
    except (UnidentifiedImageError, OSError):
        return None
    metadata = img.info
    if "workflow" not in metadata:
        return None
    return _extract_workflow_from_text(metadata.get("workflow"))


def _run_ffprobe(path: str) -> Optional[Dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _extract_workflow_from_mp4(video_path: str) -> Optional[Dict[str, Any]]:
    data = _run_ffprobe(video_path)
    if not data:
        return None
    tag_values = []
    fmt = data.get("format")
    if isinstance(fmt, dict):
        tags = fmt.get("tags")
        if isinstance(tags, dict):
            tag_values.extend(tags.values())
    streams = data.get("streams")
    if isinstance(streams, list):
        for stream in streams:
            if isinstance(stream, dict):
                tags = stream.get("tags")
                if isinstance(tags, dict):
                    tag_values.extend(tags.values())
    for value in tag_values:
        workflow = _extract_workflow_from_text(value)
        if workflow:
            return workflow
    return None


def read_metadata_and_create_json(path: str) -> None:
    ext = os.path.splitext(path)[1].lower()
    workflow_dict = None
    if ext in IMAGE_EXTS:
        workflow_dict = _extract_workflow_from_image(path)
    elif ext in VIDEO_EXTS:
        workflow_dict = _extract_workflow_from_mp4(path)
    else:
        workflow_dict = _extract_workflow_from_image(path) or _extract_workflow_from_mp4(path)

    if workflow_dict:
        _write_json_next_to(path, workflow_dict)


if __name__ == "__main__":
    images_folder = input("Path to images folder: ")
    images_folder = os.path.normpath(images_folder)

    if not os.path.isdir(images_folder):
        print(f"Path not found: {images_folder}")
        raise SystemExit(1)

    for filename in os.listdir(images_folder):
        ext = os.path.splitext(filename)[1].lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            image_path = os.path.join(images_folder, filename)
            read_metadata_and_create_json(image_path)

    input("Press Enter to exit...")
