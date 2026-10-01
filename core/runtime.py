"""Ordered installation, duplicate guard and attributable errors."""
import sys
from pathlib import Path
from .config import MOD_ID, VERSION, FONT_PATH, TSV_PATH
from .translation import read_catalog, register_language
from .fonts import register_fonts, install_language_menu
from .tags import _patch_korean_tag_glyphs, _patch_korean_shop_glyph_spacing
from .input import _patch_korean_key_names, _patch_korean_ime
from .filters import _patch_korean_filter_text, install_korean_search, install_hp_filter_label
from .editor import TranslationEditor, install_editor
from .custom import install_custom_text
from .portal import install_portal_names


def locate_game():
    preferred = [sys.modules.get(name) for name in ('__main__', 'RiftWizard3')]
    candidates = preferred + list(sys.modules.values())
    for module in candidates:
        if module is not None and all(hasattr(module, name) for name in ('PyGameView', 'make_font', 'load_saved_options')):
            return module
    raise RuntimeError('SanzuRiverr: running Rift Wizard 3 view/font interface was not found')


def _preflight(main):
    if main is None:
        raise RuntimeError('Running game module with FONT_MAP was not found')
    for file in (FONT_PATH, TSV_PATH):
        if not Path(file).is_file():
            raise RuntimeError('Required file missing: ' + str(file))
    # Read the same saved selection as the game, so conflict detection works
    # whether this derivative or the upstream mod is imported first.
    if not hasattr(main, 'load_saved_options'):
        raise RuntimeError('Game compatibility: load_saved_options is missing')
    selected = main.load_saved_options().get('enabled_mods', [])
    conflicts = {'KoreanLocalization', 'RW3KoreanRevised', 'RW3KoreanSamdoriver'}
    already_loaded = any(conflicts.intersection(name.split('.')) for name in sys.modules)
    if conflicts.intersection(selected) or already_loaded:
        raise RuntimeError('기존 KoreanLocalization·RW3KoreanRevised·RW3KoreanSamdoriver와 함께 사용할 수 없습니다. Mods에서 기존 모드를 끄고 RW3KoreanSanzuRiverr만 켠 뒤 게임을 재시작하세요.')
    cls = getattr(main, 'PyGameView', None)
    required = ('draw_with_hotkey', 'token_match', 'draw_language_menu', 'reapply_font', 'get_filter_key', 'get_shop_filter_value_key',
                'get_placeholder_description', 'get_dummy_param', 'draw_examine_portal',
                'draw_shop', 'draw_string', 'draw_search_bar',
                'process_shop_input', 'set_search_focus', 'reset_shop_page',
                'process_pick_mutator_params_input', 'set_mutator_param_search_focus', 'reset_mutator_param_filter',
                'process_combat_log_input', 'set_combat_log_query_focus', 'rebuild_combat_log_matches',
                'get_shop_filter_value_label', 'get_shop_filter_value_base_description',
                'add_shop_filter_hotkey_description', 'get_shop_filter_category_description',
                'get_shop_filter_value_target', 'get_shop_filter_clear_target',
                'get_shop_filter_category_target', 'get_shop_global_filter_target', 'draw_shop_active_filters')
    for name in required:
        if cls is None or not hasattr(cls, name):
            raise RuntimeError('Game compatibility: PyGameView.' + name + ' is missing')
    for name in ('pygame', 'format_key_name', 'ShopFilterTarget', 'make_font', 'FONT_MAP', 'FONT_SIZE_MAP', 'fit_text_to_width', 'mutators_with_params'):
        if not hasattr(main, name):
            raise RuntimeError('Game compatibility: ' + name + ' is missing')


def install():
    import Localisation as loc
    previous = getattr(loc, '_sanzuriverr_install_state', None)
    if previous:
        if previous['status'] == 'installed' and previous['version'] == VERSION:
            return previous
        raise RuntimeError('RW3KoreanSanzuRiverr has an incomplete or different installation; restart the game: ' + repr(previous))
    main = locate_game()
    _preflight(main)
    data = read_catalog()
    state = {'version': VERSION, 'status': 'installing', 'step': 'translation', 'strings': len(data)}
    loc._sanzuriverr_install_state = state
    state['step'] = 'editor_storage'
    try:
        editor = TranslationEditor(main, data)
    except Exception as exc:
        state.update(status='failed', error=repr(exc))
        raise RuntimeError('[%s] failed at editor_storage: %s' % (MOD_ID, exc)) from exc
    state['strings'] = len(data)
    # Preserve the existing feature wrapping order before adding the editor.
    def language():
        editor.language_adapter = register_language(data, None if editor.store.detection_mode == 'off' else editor.observe)
    steps = (
        ('translation', language),
        ('font', lambda: register_fonts(main)),
        ('language_menu', lambda: install_language_menu(main)),
        ('tag_glyphs', lambda: _patch_korean_tag_glyphs(main, data)),
        ('tag_spacing', lambda: _patch_korean_shop_glyph_spacing(main, data)),
        ('key_names', lambda: _patch_korean_key_names(main)),
        ('filter_text', lambda: _patch_korean_filter_text(main)),
        ('korean_search', lambda: install_korean_search(main)),
        ('hp_filter_label', lambda: install_hp_filter_label(main)),
        ('ime', lambda: _patch_korean_ime(main)),
        ('custom_text', lambda: install_custom_text(main)),
        ('portal_names', lambda: install_portal_names(main, data)),
        ('translation_editor', lambda: install_editor(main, editor)),
    )
    try:
        for name, apply in steps:
            state['step'] = name
            if apply() is False:
                raise RuntimeError('Patch target was not found')
    except Exception as exc:
        state['status'] = 'failed'
        state['error'] = repr(exc)
        raise RuntimeError('[%s] failed at %s: %s' % (MOD_ID, state['step'], exc)) from exc
    state['status'] = 'installed'
    print('[%s %s] installed %d translations' % (MOD_ID, VERSION, len(data)))
    return state
