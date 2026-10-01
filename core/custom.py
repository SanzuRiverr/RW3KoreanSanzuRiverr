"""Localisation fixes for custom-run preview templates."""
from .config import LANG_CODE


def install_custom_text(main):
    import Localisation as loc
    cls = main.PyGameView
    original = cls.get_placeholder_description

    def placeholder(self, mutator_class):
        description = original(self, mutator_class)
        if loc.get_locale() != LANG_CODE or mutator_class not in main.mutators_with_params:
            return description
        param = self.get_dummy_param(mutator_class)
        if param is None:
            return description
        instance = param() if callable(param) else param
        name = getattr(instance, 'name', str(instance))
        # The original replaces English names *after* resolving the description.
        # Repeat that replacement with the localized name in the preview only.
        localized = loc.T(str(name))
        if localized and localized != str(name):
            description = description.replace(localized, 'X')
        return description

    cls.get_placeholder_description = placeholder
    return True
