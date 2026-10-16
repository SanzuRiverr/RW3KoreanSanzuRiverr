# -*- coding: utf-8 -*-
from .config import LANG_CODE
import re
from functools import lru_cache


@lru_cache(maxsize=256)
def _hangul_search_term(term):
    return bool(re.search('[가-힣ㄱ-ㅣᄀ-ᇿ]', term))


def install_korean_search(main):
    cls = main.PyGameView
    if getattr(cls, '_sanzuriverr_search_installed', False):
        return True
    original = cls.token_match

    def token_match(view, token, term, exact):
        # Preserve explicit whole-word/phrase constraints and English searches.
        if token is not None and not exact and _hangul_search_term(term):
            return term in token
        return original(view, token, term, exact)

    cls.token_match = token_match
    cls._sanzuriverr_search_installed = True
    return True


def install_hp_filter_label(main):
    import Localisation as loc
    cls = main.PyGameView
    if getattr(cls, '_sanzuriverr_hp_filter_installed', False):
        return True
    original = cls.draw_with_hotkey

    def draw_with_hotkey(view, text, fmt_txt, surface, x, y, base_color,
                         hot_color, mouse_content=None, content_width=None,
                         center=False, font=None, hot_key=None):
        if (loc.get_locale() == LANG_CODE and fmt_txt == 'hp_cost'
                and isinstance(mouse_content, main.ShopFilterTarget)
                and base_color != hot_color):
            label = main.resolve_text(text)
            if label.startswith('HP '):
                return view.draw_string(label, surface, x, y,
                                        base_color, mouse_content=mouse_content,
                                        content_width=content_width, center=center,
                                        font=font, pre_resolved=True)
        return original(view, text, fmt_txt, surface, x, y, base_color,
                        hot_color, mouse_content=mouse_content,
                        content_width=content_width, center=center, font=font,
                        hot_key=hot_key)

    cls.draw_with_hotkey = draw_with_hotkey
    cls._sanzuriverr_hp_filter_installed = True
    return True


def _patch_korean_filter_text(main):
    """Translate filter components before formatting, including rebound keys."""
    import Localisation as loc
    if main is None or not hasattr(main, 'PyGameView'):
        return False
    cls = main.PyGameView
    if getattr(cls, '_korean_filter_text_installed', False):
        return True
    required = ('get_shop_filter_value_label', 'get_shop_filter_value_base_description',
                'add_shop_filter_hotkey_description', 'get_shop_filter_category_description',
                'get_shop_filter_value_target', 'get_shop_filter_clear_target',
                'get_shop_filter_category_target', 'get_shop_global_filter_target',
                'draw_shop_active_filters')
    for method in required:
        if not hasattr(cls, method):
            raise RuntimeError('Korean filter text: missing PyGameView.' + method)
    originals = {name: getattr(cls, name) for name in required}
    def korean():
        return loc.get_locale() == LANG_CODE
    def resolve(message, **fmt):
        return main.resolve_text((message, fmt)) if fmt else main.resolve_text(message)

    def value_label(view, category, value):
        label = originals['get_shop_filter_value_label'](view, category, value)
        return resolve(label) if korean() else label

    templates = {
        main.SHOP_FILTER_TAGS: 'Results with {label} tag',
        main.SHOP_FILTER_ATTR: 'Results with {label}',
        main.SHOP_FILTER_BONUS: 'Results boost {label}',
        main.SHOP_FILTER_GAIN_TAGS: 'Results can gain {label} tag',
        main.SHOP_FILTER_GAIN_ATTR: 'Results can gain {label}',
        main.SHOP_FILTER_RECIPE_TAGS: 'Results require {label}',
    }
    def base_description(view, category, value):
        if not korean():
            return originals['get_shop_filter_value_base_description'](view, category, value)
        label = view.get_shop_filter_value_label(category, value)
        return resolve(templates.get(category, 'Toggle this filter'), label=label)

    def hotkey_description(view, desc, hotkey):
        if not korean():
            return originals['add_shop_filter_hotkey_description'](view, desc, hotkey)
        if not hotkey:
            return resolve(desc)
        return resolve('{desc}\nHotkey:  {hotkey}', desc=resolve(desc), hotkey=main.Raw(hotkey))

    def category_description(view, category):
        if not korean():
            return originals['get_shop_filter_category_description'](view, category)
        desc = {main.SHOP_FILTER_TAGS: 'Filter tags', main.SHOP_FILTER_ATTR: 'Filter attributes',
                main.SHOP_FILTER_BONUS: 'Filter attribute bonuses'}.get(category, 'Show filters')
        hotkey = view.get_shop_filter_category_hotkey_description(category)
        if hotkey:
            return resolve('{desc}\nNext category:  {hotkey}', desc=resolve(desc), hotkey=main.Raw(hotkey))
        return resolve(desc)

    def wrap_target(name, value_target=False):
        original = originals[name]
        def target(view, category, *args, **kwargs):
            result = original(view, category, *args, **kwargs)
            if korean():
                if value_target:
                    value = args[0] if args else kwargs['value']
                    result.name = resolve('{cat}: {label}', cat=main.shop_filter_category_names[category],
                                          label=view.get_shop_filter_value_label(category, value))
                else:
                    result.name = resolve(result.name)
                result.description = main.resolve_text(result.description)
            return result
        return target

    def active_filters(view, panel, x, y):
        if not korean():
            return originals['draw_shop_active_filters'](view, panel, x, y)
        for category, value in view.get_active_shop_filter_chips():
            target = view.get_shop_filter_clear_target(category, value)
            color = view.get_shop_filter_value_color(category, value)
            view.draw_string(target.name, panel, x, y, color, mouse_content=target, pre_resolved=True)
            y += view.linesize
        return y

    cls.get_shop_filter_value_label = value_label
    cls.get_shop_filter_value_base_description = base_description
    cls.add_shop_filter_hotkey_description = hotkey_description
    cls.get_shop_filter_category_description = category_description
    for name in ('get_shop_filter_value_target', 'get_shop_filter_clear_target'):
        setattr(cls, name, wrap_target(name, True))
    for name in ('get_shop_filter_category_target', 'get_shop_global_filter_target'):
        setattr(cls, name, wrap_target(name))
    cls.draw_shop_active_filters = active_filters
    cls._korean_filter_text_installed = True
    return True
