# rename_to_english.py
import os
import re
import sys

try:
    from deep_translator import GoogleTranslator
except Exception:  # pragma: no cover
    GoogleTranslator = None

try:
    from unidecode import unidecode
except Exception:  # pragma: no cover
    unidecode = None

DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))

SAFE_RE = re.compile(r"[^A-Za-z0-9 ._\-()&]")
SPACE_RE = re.compile(r"\s+")


def needs_translate(name: str) -> bool:
    return any(ord(ch) > 127 for ch in name)


def clean_name(text: str) -> str:
    text = text.replace('+', ' plus ')
    text = text.replace('&', ' and ')
    text = text.replace('/', ' ')
    text = text.replace('\\', ' ')
    text = text.replace(':', ' ')
    text = text.replace('?', '')
    text = text.replace('!', '')
    text = text.replace('"', '')
    text = text.replace("'", '')
    text = SAFE_RE.sub(' ', text)
    text = SPACE_RE.sub(' ', text).strip()
    if not text:
        return "item"
    return text


def build_translator():
    if GoogleTranslator is None:
        return None
    try:
        return GoogleTranslator(source='auto', target='en')
    except Exception:
        return None


TRANSLATOR = build_translator()
CACHE = {}


def translate_text(text: str) -> str:
    if text in CACHE:
        return CACHE[text]
    translated = None
    if TRANSLATOR is not None:
        try:
            translated = TRANSLATOR.translate(text)
        except Exception:
            translated = None
    if translated is None:
        if unidecode is not None:
            translated = unidecode(text)
        else:
            translated = text
    translated = clean_name(translated)
    CACHE[text] = translated
    return translated


def rename_all(root: str) -> list[tuple[str, str]]:
    renamed = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if os.path.basename(dirpath) == '.git' or os.path.sep + '.git' + os.path.sep in dirpath:
            continue

        for fname in filenames:
            if not needs_translate(fname):
                continue
            src = os.path.join(dirpath, fname)
            if not os.path.exists(src):
                continue
            stem, ext = os.path.splitext(fname)
            new_stem = translate_text(stem)
            new_name = f"{new_stem}{ext}"
            if new_name == fname:
                continue
            target = os.path.join(dirpath, new_name)
            if os.path.exists(target):
                i = 1
                while True:
                    candidate = f"{new_name} {i}"
                    target = os.path.join(dirpath, candidate)
                    if not os.path.exists(target):
                        new_name = candidate
                        break
                    i += 1
            os.rename(src, target)
            renamed.append((src, target))

        for dname in dirnames:
            if dname == '.git':
                continue
            if not needs_translate(dname):
                continue
            src = os.path.join(dirpath, dname)
            if not os.path.exists(src):
                continue
            new_name = translate_text(dname)
            if new_name == dname:
                continue
            target = os.path.join(dirpath, new_name)
            if os.path.exists(target):
                i = 1
                while True:
                    candidate = f"{new_name} {i}"
                    target = os.path.join(dirpath, candidate)
                    if not os.path.exists(target):
                        new_name = candidate
                        break
                    i += 1
            os.rename(src, target)
            renamed.append((src, target))

    return renamed


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    root = DEFAULT_ROOT
    if len(sys.argv) > 1:
        root = os.path.abspath(sys.argv[1])
    if not os.path.isdir(root):
        print(f"error: folder not found: {root}")
        return 2
    log_path = os.path.join(root, "rename_to_english.log")
    renamed = rename_all(root)
    with open(log_path, 'w', encoding='utf-8') as f:
        for src, dst in renamed:
            f.write(f"{src} -> {dst}\n")
    print(f"renamed={len(renamed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
