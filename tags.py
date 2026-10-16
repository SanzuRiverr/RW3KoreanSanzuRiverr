# -*- coding: utf-8 -*-
from .config import LANG_CODE, KO_SHOP_TAG_SHIFT


def _patch_korean_tag_glyphs(main, ko_data):
    """Use the first Hangul character as the visible tag/recipe glyph.

    Keyboard bindings and explicit shortcut help remain unchanged. Tag labels,
    tag chips, component counters, and crafting recipes display the localized
    first character in the tag color.
    """
    if main is None:
        return False
    import Localisation as loc

    target_cls = None
    for obj in vars(main).values():
        if (isinstance(obj, type)
                and "get_filter_key" in obj.__dict__
                and "get_shop_filter_value_key" in obj.__dict__):
            target_cls = obj
            break
    if target_cls is None:
        return False

    original_get_filter_key = target_cls.get_filter_key
    def patched_get_filter_key(self, filter_obj):
        if loc.get_locale() == LANG_CODE:
            english = getattr(filter_obj, "name", None)
            # Any is a wildcard recipe slot; keep the familiar asterisk.
            if english == "Any":
                return original_get_filter_key(self, filter_obj)
            label = ko_data.get(english) if isinstance(english, str) else None
            if label:
                return label[0]
        return original_get_filter_key(self, filter_obj)

    target_cls.get_filter_key = patched_get_filter_key
    return True


def _patch_korean_shop_glyph_spacing(main, ko_data):
    """Keep consecutive localized tag glyphs from overlapping in shop rows.

    The base draw_shop advances by the width of the English tag initial even
    after get_filter_key has supplied a wider Hangul glyph.  Intercept only
    localized one-character tag/recipe draws and move a glyph to the previous
    glyph's actual right edge when the base coordinates overlap.
    """
    if main is None:
        return False
    import Localisation as loc

    target_cls = None
    for obj in vars(main).values():
        if (isinstance(obj, type)
                and "draw_shop" in obj.__dict__
                and "draw_string" in obj.__dict__
                and "get_filter_key" in obj.__dict__):
            target_cls = obj
            break
    if target_cls is None:
        return False

    glyphs = {value[0] for value in ko_data.values()
              if isinstance(value, str) and value}
    glyphs.add("*")
    original_draw_shop = target_cls.draw_shop
    original_draw_string = target_cls.draw_string

    def patched_draw_shop(self, *args, **kwargs):
        self._ko_shop_glyph_ends = {}
        self._ko_shop_column_rows = set()
        self._ko_shop_glyph_spacing = True
        try:
            return original_draw_shop(self, *args, **kwargs)
        finally:
            self._ko_shop_glyph_spacing = False
            self._ko_shop_glyph_ends = {}
            self._ko_shop_column_rows = set()

    def patched_draw_string(self, string, surface, x, y, *args, **kwargs):
        # Filter labels share the list surface and row coordinates, but are
        # independent of the tag/recipe column, even when their text is one glyph.
        mouse_content = args[1] if len(args) > 1 else kwargs.get("mouse_content")
        if isinstance(mouse_content, main.ShopFilterTarget):
            return original_draw_string(self, string, surface, x, y, *args, **kwargs)
        # Only the tags/recipe column of the two requested shop screens moves.
        # Names identify actual option rows, excluding the ingredient boxes below.
        if (loc.get_locale() == LANG_CODE
                and getattr(self, "_ko_shop_glyph_spacing", False)
                and surface is getattr(self, "middle_menu_display", None)
                and getattr(self, "shop_type", None) in (main.SHOP_TYPE_SPELLS, main.SHOP_TYPE_CRAFTING)):
            content_width = args[2] if len(args) > 2 else kwargs.get("content_width")
            if content_width == main.SPRITE_SIZE * 7 and x == main.SPRITE_SIZE:
                self._ko_shop_column_rows.add(y)
            header = "Tags" if self.shop_type == main.SHOP_TYPE_SPELLS else "Recipe"
            is_header = string == header and x == main.SPRITE_SIZE * 8
            is_column = (y in self._ko_shop_column_rows and isinstance(string, str)
                         and len(string) == 1 and string in glyphs
                         and x >= main.SPRITE_SIZE * 8)
            if is_header or is_column:
                x -= KO_SHOP_TAG_SHIFT
        if (loc.get_locale() == LANG_CODE
                and getattr(self, "_ko_shop_glyph_spacing", False)
                and surface is getattr(self, "middle_menu_display", None)
                and isinstance(string, str)
                and len(string) == 1
                and string in glyphs):
            row_key = (id(surface), y)
            previous_end = self._ko_shop_glyph_ends.get(row_key)
            if previous_end is not None and x < previous_end:
                x = previous_end
            font = kwargs.get("font") or getattr(self, "font", None)
            if font is not None:
                self._ko_shop_glyph_ends[row_key] = x + font.size(string)[0]
        return original_draw_string(self, string, surface, x, y, *args, **kwargs)

    target_cls.draw_shop = patched_draw_shop
    target_cls.draw_string = patched_draw_string
    return True
