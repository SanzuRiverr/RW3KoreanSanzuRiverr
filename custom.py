"""Localisation fixes for custom-run preview templates."""
from .config import LANG_CODE


def install_custom_text(main):
    import Localisation as loc
    install_periodic_rule_name(loc)
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


        localized = loc.T(str(name))
        if localized and localized != str(name):
            description = description.replace(localized, 'X')
        return description

    cls.get_placeholder_description = placeholder
    return True


def install_periodic_rule_name(loc):
    """Resolve the configured interval on access, including existing save objects."""
    from Mutators import EveryXTurnsBuff
    if isinstance(EveryXTurnsBuff.__dict__.get('name'), property):
        return

    def name(self):
        original = self.__dict__.get('name', 'EveryXTurnsBuff')
        if loc.get_locale() != LANG_CODE or original != 'EveryXTurnsBuff':
            return original
        return loc.T('Every {interval} turns: trigger mutator rule',
                     interval=getattr(self, 'interval', 'X'))

    def set_name(self, value):
        self.__dict__['name'] = value

    EveryXTurnsBuff.name = property(name, set_name)
