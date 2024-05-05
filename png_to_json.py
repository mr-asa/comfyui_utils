import os
import json
from PIL import Image

def read_exif_and_create_json(image_path):
    # Открываем изображение
    img = Image.open(image_path)
    # print("Image.open(image_path)",image_path)

    # Получаем метаданные EXIF
    metadata = img.info
    # print("metadata",metadata)

    # Проверяем, есть ли ключ "workflow" в метаданных
    if "workflow" in metadata:
        workflow_value = metadata["workflow"]

        try:
            workflow_dict = json.loads(workflow_value)
        except json.JSONDecodeError:
            print(f"Ошибка при чтении JSON для {image_path}")
            return

        if workflow_dict:
            # Создаем JSON файл с именем изображения и записываем значение workflow
            json_file_path = os.path.splitext(image_path)[0] + ".json"
            with open(json_file_path, "w", encoding="utf-8") as json_file:
                json.dump(workflow_dict, json_file, ensure_ascii=False, indent=4)  # Отступы для лучшей читаемости

            print(f"--> {image_path}")

# Папка с изображениями
images_folder = input(f"Path to images folder: ")
images_folder = os.path.normpath(images_folder)
# images_folder = "f:/ComfyUI/workflows_collection/Collection/was_suite/"

# Проходим по всем изображениям в папке
for filename in os.listdir(images_folder):
    # print("---\nfilename: ",filename,)
    if filename.endswith(".jpg") or filename.endswith(".jpeg") or filename.endswith(".png"):
        image_path = os.path.join(images_folder, filename)
        # print("----image_path",image_path)
        read_exif_and_create_json(image_path)
    
    # break


input("Нажмите Enter для завершения работы...")