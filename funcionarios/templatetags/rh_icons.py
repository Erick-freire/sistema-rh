from django import template
from django.utils.safestring import mark_safe

register = template.Library()

ICONS = {
    'home': '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9"/>',
    'users': '<circle cx="9" cy="8" r="3"/><path d="M3 20v-.5A5 5 0 0 1 8 15.5h2a5 5 0 0 1 5 5v.5"/><circle cx="17.5" cy="9" r="2.3"/><path d="M16 20v-.4a4.4 4.4 0 0 0-1.4-3.2"/>',
    'umbrella': '<path d="M12 3a8 8 0 0 1 8 8H4a8 8 0 0 1 8-8Z"/><path d="M12 11v7a2 2 0 0 1-2 2"/><path d="M12 3v1.5"/>',
    'check-square': '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8.5 12.5 2.5 2.5 5-5.5"/>',
    'user': '<circle cx="12" cy="8" r="3.5"/><path d="M5 20v-.5A5.5 5.5 0 0 1 10.5 14h3A5.5 5.5 0 0 1 19 19.5v.5"/>',
    'upload': '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    'folder': '<path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h4l2 2.5h7A1.5 1.5 0 0 1 20 9v8.5A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5Z"/>',
    'pen': '<path d="m4 20 .8-3.6L16 5.2a1.8 1.8 0 0 1 2.5 0l.3.3a1.8 1.8 0 0 1 0 2.5L7.6 19.2Z"/><path d="m14.5 6.7 2.8 2.8"/>',
    'calendar-plus': '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16"/><path d="M8 3v4"/><path d="M16 3v4"/><path d="M12 13v5"/><path d="M9.5 15.5h5"/>',
    'log-out': '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="m10 16 4-4-4-4"/><path d="M14 12H3"/>',
    'menu': '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
    'x': '<path d="m5 5 14 14"/><path d="m19 5-14 14"/>',
    'bell': '<path d="M12 3.5a5 5 0 0 0-5 5v3.2c0 .6-.2 1.2-.6 1.7L5 15.5h14l-1.4-2.1c-.4-.5-.6-1.1-.6-1.7V8.5a5 5 0 0 0-5-5Z"/><path d="M9.5 18.5a2.5 2.5 0 0 0 5 0"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.8-4.8"/>',
    'download': '<path d="M12 4v11"/><path d="m7.5 11 4.5 4.5L16.5 11"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    'eye': '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.6"/>',
    'copy': '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    'chevron-right': '<path d="m9 5 7 7-7 7"/>',
    'plus': '<path d="M12 5v14"/><path d="M5 12h14"/>',
    'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.5 2.5L16 9.5"/>',
    'camera': '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h1.6l.9-1.5A1.5 1.5 0 0 1 9.3 4.7h5.4a1.5 1.5 0 0 1 1.3.8L17 7h1.5A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5Z"/><circle cx="12" cy="13" r="3.4"/>',
    'file': '<path d="M6 3.5h7l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-16a1 1 0 0 1 1-1Z"/><path d="M13 3.5V8h4.5"/>',
    'alert-triangle': '<path d="M12 4.2 21 19.5H3Z"/><path d="M12 10v4.2"/><path d="M12 17.3h.01"/>',
    'arrow-left': '<path d="M19 12H5"/><path d="m11 18-6-6 6-6"/>',
    'dots': '<circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
}


@register.simple_tag
def icon(name, css_class=''):
    inner = ICONS.get(name, '')
    classes = f'icon {css_class}'.strip()
    return mark_safe(
        f'<svg class="{classes}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{inner}</svg>'
    )
