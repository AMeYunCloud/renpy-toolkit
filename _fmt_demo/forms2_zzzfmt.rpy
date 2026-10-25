init offset = 1

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
                _groups = tuple(_original_translate_string(g) for g in _m.groups())
                return _tpl.format(*_groups)
        return _original_translate_string(s)
    renpy.translation.translate_string = _rtk_translate

translate None python:
    renpy.translation.translate_string = _original_translate_string

# item names so the captured group can resolve
translate zzzfmt strings:
    old "Sandwich"
    new "三明治"
