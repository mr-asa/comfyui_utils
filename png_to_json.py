import os
import json
from PIL import Image


def read_exif_and_create_json(image_path: str) -> None:
    # Open the image and read metadata
    img = Image.open(image_path)
    metadata = img.info

    # Extract workflow metadata if present and write to JSON next to the image
    if "workflow" in metadata:
        workflow_value = metadata["workflow"]
        try:
            workflow_dict = json.loads(workflow_value)
        except json.JSONDecodeError:
            print(f"Error reading JSON for {image_path}")
            return

        if workflow_dict:
            json_file_path = os.path.splitext(image_path)[0] + ".json"
            with open(json_file_path, "w", encoding="utf-8") as json_file:
                json.dump(workflow_dict, json_file, ensure_ascii=False, indent=4)

            print(f"--> {image_path}")


if __name__ == "__main__":
    images_folder = input("Path to images folder: ")
    images_folder = os.path.normpath(images_folder)

    for filename in os.listdir(images_folder):
        if filename.lower().endswith((".jpg", ".jpeg", ".png")):
            image_path = os.path.join(images_folder, filename)
            read_exif_and_create_json(image_path)

    input("Press Enter to exit...")

