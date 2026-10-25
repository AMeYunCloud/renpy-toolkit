# ---- form B: hardcoded font ----
init offset = 1

init python:
    _original_font_replacement_map = config.font_replacement_map.copy()

translate zzzfmt python:
    config.font_replacement_map["fonts/big_noodle_titling.ttf", False, False] = ("tl/zzzfmt/fonts/TBX.ttf", False, False)

translate None python:
    config.font_replacement_map.clear()
    config.font_replacement_map = _original_font_replacement_map.copy()

# ---- form C: runtime wrapper ----
init python:
    _original_translate_string = renpy.translation.translate_string

translate zzzfmt python:
    import re as _rtk_re
    _rtk_rules = [
        (_rtk_re.compile(r"^You bought 1 (.+)!$"), "你买了 1 个{0}！"),
    ]
    def _rtk_translate(s):
        for _pat, _tpl in _rtk_rules:
            _m = _pat.match(s)
            if _m:
                return _tpl.format(*_m.groups())
        return _original_translate_string(s)
    renpy.translation.translate_string = _rtk_translate

translate None python:
    renpy.translation.translate_string = _original_translate_string

# ---- form A: hardcoded string with marker ----
# hardcoded: confirm screen
# game/screens.rpy:120
translate zzzfmt strings:
    old "Are you sure?"
    new "确定吗？"
