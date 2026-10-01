"""Translate portal preview names that the game truncates before localisation."""
from .config import LANG_CODE
from .translation import composed_label
from .proper_names import load_proper_names
from .composition import composition_data


def portal_aliases(catalog):
    # Only unambiguous aliases are usable; never guess between matching names.
    aliases = {}
    names = set(catalog)
    names.update(composition_data().get('generated_names', ()))
    for name, entry in load_proper_names().items():
        if 'unit' in entry['kinds']:
            if catalog.get(name) or composed_label(name, catalog):
                names.add(name)
    for name in names:
        if 20 < len(name) <= 100 and '\n' not in name and '{' not in name:
            alias = name[:18] + '..'
            aliases[alias] = name if alias not in aliases else None
    return aliases


def install_portal_names(main, catalog):
    import Localisation as loc
    cls = main.PyGameView
    original_portal = cls.draw_examine_portal
    original_draw = cls.draw_string
    aliases = portal_aliases(catalog)

    def portal(self, *args, **kwargs):
        previous = getattr(self, '_ko_portal_names', False)
        self._ko_portal_names = True
        try:
            return original_portal(self, *args, **kwargs)
        finally:
            self._ko_portal_names = previous

    def draw(self, text, surface, x, y, *args, **kwargs):
        if (loc.get_locale() == LANG_CODE and getattr(self, '_ko_portal_names', False)
                and surface is self.examine_display and isinstance(text, str)
                and aliases.get(text)):
            font = kwargs.get('font') or (args[5] if len(args) > 5 else None) or self.font
            translated = loc.T(aliases[text])
            text = main.fit_text_to_width(font, translated, surface.get_width() - x - 8)
            kwargs['pre_resolved'] = True
        return original_draw(self, text, surface, x, y, *args, **kwargs)

    cls.draw_examine_portal = portal
    cls.draw_string = draw
    return True
