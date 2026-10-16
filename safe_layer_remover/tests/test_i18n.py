"""Bilingual UI: every tr() source string has an English translation with the same placeholders."""

import ast
import os
import re
import string
import unittest

import utilities  # noqa: F401

from safe_layer_remover.core import texts
from safe_layer_remover.i18n.en import EN

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# strings passed to tr() through a variable (not a literal)
INDIRECT = [
    "törölhető", "figyelmeztetés", "ellenőrizendő", "nem törölhető", "zárolt / hibás",
    "Memóriarétegek adatvesztése", "Mentetlen szerkesztések", "Nyomtatási elrendezések",
    "Projekt terepmodell", "Szelektív maszkolás", "&Biztonságos rétegtörlés",
]


def source_strings() -> set[str]:
    found: set[str] = set(INDIRECT)
    for root, dirs, files in os.walk(PLUGIN_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
        for name in files:
            if not name.endswith(".py") or name == "en.py":
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "tr"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    found.add(node.args[0].value)
    return found


def placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None}


class I18nTest(unittest.TestCase):
    def tearDown(self):
        texts.set_ui_language("hu")

    def test_every_string_translated(self):
        missing = sorted(source_strings() - set(EN))
        self.assertEqual(missing, [], "untranslated strings")

    def test_placeholders_match(self):
        bad = [k for k, v in EN.items() if placeholders(k) != placeholders(v)]
        self.assertEqual(bad, [])

    def test_runtime_switch(self):
        texts.set_ui_language("en")
        self.assertEqual(texts.tr("Törölhető"), "Removable")
        self.assertEqual(texts.with_article("Övezetek", capital=True), "Övezetek")
        self.assertEqual(texts.tr("{a} joinolja").format(a=texts.with_article("Telkek")), "joined by Telkek")
        texts.set_ui_language("hu")
        self.assertEqual(texts.tr("Törölhető"), "Törölhető")
        self.assertEqual(texts.with_article("Övezetek"), "az Övezetek")

    def test_english_analysis_texts(self):
        from utilities import analyze, mk, new_project
        from test_detectors import add_join
        from safe_layer_remover.ui.explain import layer_short_reason

        texts.set_ui_language("en")
        new_project()
        a, b = mk("Telkek"), mk("Parcellak")
        add_join(a, b)
        r = analyze()
        reason = layer_short_reason(r.infos[b.id()], r)
        self.assertEqual(reason, "joined by Telkek")
        self.assertEqual(r.infos[b.id()].badges[0].text, "Memory")


if __name__ == "__main__":
    unittest.main()
