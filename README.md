Some random utilities for quick checks.

Copy repository to `...\{ComfyUI_folder}\{Any_name}` (**other_gits**) for example.\
I wrote all this on the ComfyUI cloned git repository, not the portable one.

---

### update.py
   
Automatic update comfyui and all repositories in the `...\{ComfyUI_folder}\custom_nodes\` directory.\
Writes logs of all changes made (commit comments) and changes in files.

---
### requirements_check.py
  
Compares the version of the installed package with the latest one.\
Prescribes a list of required packages in requrements.txt and the ability to update them.

   
---
### png_to_json.py

Convert all workflow from .jpeg and .png metadata to .json files in chosen folder.
