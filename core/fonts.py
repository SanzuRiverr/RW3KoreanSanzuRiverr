"""Use the game's own language-menu layout with language-aware font metrics."""
from functools import wraps
from .config import LANG_CODE, FONT_PATH, KO_FONT_SIZE


class MenuMeasurements:
    """Only row measurement changes; rendering receives actual Pygame fonts."""
    def __init__(self, default_font, by_label):
        self.default_font = default_font
        self.by_label = by_label

    def size(self, text):
        return self.by_label.get(text, self.default_font).size(text)

    def __getattr__(self, name):
        return getattr(self.default_font, name)


def register_fonts(game):
    mappings = ((game.FONT_MAP, FONT_PATH), (game.FONT_SIZE_MAP, KO_FONT_SIZE))
    for mapping, value in mappings:
        if not isinstance(mapping, dict):
            raise TypeError('Game font registry must be a dictionary')
    for mapping, value in mappings:
        mapping[LANG_CODE] = value
    install_font_layout(game)


def install_font_layout(game):
    """Refresh font-dependent pagination after changing language."""
    cls = game.PyGameView
    original = cls.reapply_font
    original_init = cls.__init__

    def refresh_layout(view):
        if not hasattr(view, 'max_shop_objects'):
            return
        height = view.middle_menu_display.get_height()
        # The game places page controls at (max_shop_objects + 4) rows.
        # Reserve the controls' own row as well as the bottom margin.
        rows = max(1, (height - view.border_margin) // view.linesize - 5)
        reserved = max(0, view.max_shop_objects - view.shop_rows_per_page)
        view.max_shop_objects = rows
        view.shop_rows_per_page = max(1, rows - reserved)
        view.max_keybinds_per_page = max(1, rows - 1)

    @wraps(original_init)
    def initialize(view, *args, **kwargs):
        original_init(view, *args, **kwargs)
        # The constructor writes its legacy page sizes after reapply_font.
        refresh_layout(view)

    @wraps(original)
    def reapply(view, *args, **kwargs):
        old_linesize = getattr(view, 'linesize', None)
        result = original(view, *args, **kwargs)
        refresh_layout(view)
        if old_linesize != view.linesize:
            view.shop_page = 0
            view.key_bind_page = 0
        return result

    cls.reapply_font = reapply
    cls.__init__ = initialize


def install_language_menu(game):
    import Localisation
    view_type = game.PyGameView
    game_draw = view_type.draw_language_menu
    fonts = {}

    @wraps(game_draw)
    def draw_menu(view):
        codes = view.langs_cache or Localisation.available_locales() or ['en']
        by_code = {}
        for code in codes:
            key = (code, view.font_px)
            if key not in fonts:
                try:
                    fonts[key] = game.make_font(code, view.font_px)
                except Exception as exc:
                    raise RuntimeError(f'SanzuRiverr language menu: font load failed for {code!r}') from exc
            by_code[code] = fonts[key]
        base_font = view.font
        base_draw = view.draw_string
        had_draw = 'draw_string' in vars(view)
        saved_draw = vars(view).get('draw_string')
        labels = {Localisation.LANG_DISPLAY_NAME.get(code, code): font for code, font in by_code.items()}

        def draw_row(*args, **kwargs):
            kwargs.setdefault('font', by_code.get(kwargs.get('mouse_content'), base_font))
            if kwargs.get('mouse_content') not in by_code:
                return base_draw(*args, **kwargs)
            # Language names must stay in their own script and matching font.
            # Bypass both the game's translator and display-time corrections.
            kwargs['pre_resolved'] = True
            previous = getattr(view, '_sanzuriverr_native_language_row', False)
            view._sanzuriverr_native_language_row = True
            try:
                return base_draw(*args, **kwargs)
            finally:
                view._sanzuriverr_native_language_row = previous

        view.font = MenuMeasurements(base_font, labels)
        view.draw_string = draw_row
        try:
            # Keep game navigation, centering, spacing and hitbox generation.
            return game_draw(view)
        finally:
            view.font = base_font
            if had_draw:
                view.draw_string = saved_draw
            else:
                del view.draw_string

    view_type.draw_language_menu = draw_menu
