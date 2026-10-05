# -*- coding: utf-8 -*-
from .config import LANG_CODE


def _patch_korean_key_names(main):
    """Translate directional and numpad labels without changing bindings."""
    if main is None or not hasattr(main, "format_key_name"):
        return False
    import Localisation as loc

    original_format_key_name = main.format_key_name
    def patched_format_key_name(keycode):
        if loc.get_locale() == LANG_CODE:
            pygame = getattr(main, "pygame", None)
            if pygame is not None:
                arrows = {
                    pygame.K_UP: "위 방향키",
                    pygame.K_DOWN: "아래 방향키",
                    pygame.K_LEFT: "좌 방향키",
                    pygame.K_RIGHT: "우 방향키",
                }
                if keycode in arrows:
                    return arrows[keycode]
                if keycode in getattr(main, "NUMPAD_KEYS", set()):
                    name = pygame.key.name(keycode).upper()
                    return "숫자 패드 %s" % name
        return original_format_key_name(keycode)

    main.format_key_name = patched_format_key_name
    return True


def _patch_korean_ime(main):
    """Keep IME preedit separate while exposing the visible query to filters."""
    import Localisation as loc
    if main is None or not hasattr(main, 'PyGameView'):
        return False
    cls = main.PyGameView
    pg = main.pygame
    specs = (
        ('process_shop_input', 'set_search_focus', 'search_focused', 'search_query', 'reset_shop_page'),
        ('process_pick_mutator_params_input', 'set_mutator_param_search_focus', 'mutator_param_search_focused', 'mutator_param_query', 'reset_mutator_param_filter'),
        ('process_combat_log_input', 'set_combat_log_query_focus', 'combat_log_query_focused', 'combat_log_query', 'rebuild_combat_log_matches'),
    )
    for process, focus, _, _, refresh in specs:
        for method in (process, focus, refresh, 'draw_search_bar'):
            if not hasattr(cls, method):
                raise RuntimeError('Korean IME: required View method missing: ' + method)
    if getattr(cls, '_korean_ime_installed', False):
        return True

    def states(view):
        if not hasattr(view, '_ko_ime_states'):
            view._ko_ime_states = {}
        return view._ko_ime_states

    def wrap_input(original, focused_attr, query_attr, refresh):
        def process(view, *args, **kwargs):
            state = states(view).setdefault(query_attr, {'text': '', 'visible': None, 'start': 0, 'length': 0})
            if loc.get_locale() != LANG_CODE or not getattr(view, focused_attr, False):
                state['text'] = ''
                return original(view, *args, **kwargs)
            previous = getattr(view, query_attr)
            composition = state['text'] if state['visible'] == previous else ''
            committed = previous[:-len(composition)] if composition else previous
            events = view.events
            # Commit/empty-preedit events and the Enter key can share one frame.
            # In that frame IME owns editing keys even after composition clears.
            ime_frame = bool(composition) or any(e.type == pg.TEXTEDITING for e in events)
            edit_keys = {pg.K_BACKSPACE, pg.K_DELETE, pg.K_LEFT, pg.K_RIGHT, pg.K_UP, pg.K_DOWN, pg.K_HOME, pg.K_END}
            edit_keys.update(view.key_binds[main.KEY_BIND_CONFIRM])
            edit_keys.update(view.key_binds[main.KEY_BIND_ABORT])
            filtered = []
            for event in events:
                if event.type == pg.TEXTEDITING:
                    composition = event.text
                    state['start'] = max(0, min(len(composition), event.start))
                    state['length'] = max(0, event.length)
                elif event.type == pg.TEXTINPUT:
                    committed += event.text
                    composition = ''
                elif event.type == pg.WINDOWFOCUSLOST:
                    composition = ''
                    view.repeat_keys.clear()
                    filtered.append(event)
                elif event.type == pg.KEYDOWN and ime_frame and event.key in edit_keys:
                    view.repeat_keys.pop(event.key, None)
                else:
                    filtered.append(event)
            if ime_frame:
                # The game also synthesizes repeat events before this handler.
                for key in edit_keys:
                    view.repeat_keys.pop(key, None)
            visible = committed + composition
            state.update(text=composition, visible=visible)
            setattr(view, query_attr, visible)
            if previous != visible:
                getattr(view, refresh)()
            view.events = filtered
            try:
                return original(view, *args, **kwargs)
            finally:
                view.events = events
                if getattr(view, query_attr) != state['visible'] or not getattr(view, focused_attr, False):
                    state.update(text='', visible=getattr(view, query_attr))
        return process

    def wrap_focus(original, query_attr):
        def focus(view, focused, clear=False):
            state = states(view).get(query_attr)
            if state is not None and (clear or not focused):


                state.update(text='', visible=None)
            if getattr(view, query_attr, None) is not None and not focused:
                view.repeat_keys.clear()
            return original(view, focused, clear=clear)
        return focus

    for process, focus, focused_attr, query_attr, refresh in specs:
        setattr(cls, process, wrap_input(getattr(cls, process), focused_attr, query_attr, refresh))
        setattr(cls, focus, wrap_focus(getattr(cls, focus), query_attr))

    original_draw = cls.draw_search_bar
    def draw_search_bar(view, panel, x, y, query, focused):
        if loc.get_locale() != LANG_CODE or not focused:
            return original_draw(view, panel, x, y, query, focused)

        label = main.resolve_text('Search:')
        view.draw_string(label, panel, x, y, pre_resolved=True)
        bar = main.get_image(['ui', 'search_bar'])
        height = view.font.get_height()
        rect = pg.Rect(x + main.cached_size(view.font, label), int(y - 2 + height / 2 - bar.get_height() / 2), *bar.get_size())
        panel.blit(bar, rect.topleft)
        view.make_content_rect(panel, rect, main.SEARCH_INPUT_TARGET)
        overlay = pg.Surface(rect.size, pg.SRCALPHA)
        overlay.fill((255, 255, 255, 40))
        panel.blit(overlay, rect)
        inner = pg.Rect(rect.x + 12, rect.y + (rect.height - height) // 2, rect.width - 24, height + 2)
        state = None
        for _, _, focused_attr, query_attr, _ in specs:
            candidate = states(view).get(query_attr)
            if (getattr(view, focused_attr, False) and getattr(view, query_attr, None) == query
                    and candidate and candidate['visible'] == query):
                state = candidate
                break
        composition = state['text'] if state else ''
        committed_length = len(query) - len(composition)
        cursor_index = len(query)
        if composition:
            cursor_index = committed_length + min(len(composition), state['start'] + state['length'])
        cursor_width = view.font.size(query[:cursor_index])[0]
        scroll = max(0, cursor_width - inner.width + 3)
        text_x = inner.x - scroll
        old_clip = panel.get_clip()
        panel.set_clip(old_clip.clip(inner))
        try:
            if query:
                panel.blit(view.font.render(query, True, (255, 255, 255)), (text_x, inner.y))
            if composition:
                start_x = text_x + view.font.size(query[:committed_length])[0]
                end_x = text_x + view.font.size(query)[0]
                pg.draw.line(panel, (255, 220, 120), (start_x, inner.bottom - 2), (end_x, inner.bottom - 2), 1)
            if composition or (pg.time.get_ticks() // 500) % 2 == 0:
                caret_x = text_x + cursor_width
                pg.draw.line(panel, (255, 255, 255), (caret_x, inner.y), (caret_x, inner.y + height - 2), 1)
        finally:
            panel.set_clip(old_clip)
    cls.draw_search_bar = draw_search_bar
    cls._korean_ime_installed = True
    return True
