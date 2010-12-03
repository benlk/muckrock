"""
General temaplate tags
"""

from django import template
from django.template.defaultfilters import stringfilter

import re

register = template.Library()

@register.simple_tag
def active(request, pattern):
    """Check url against pattern to determine active css attribute"""
    pattern = pattern.replace('{{user}}', str(request.user))
    if re.search(pattern, request.path):
        return 'current-tab'
    return ''

@register.simple_tag
def page_links(page_obj, order=None, field=None):
    """Return page links for surrounding pages"""

    def make_link(num, skip):
        """Make a link to page num"""
        options = ''
        if order:
            options += '&order=%s' % order
        if field:
            options += '&field=%s' % field
        if num != skip:
            return '<a href="?page=%d%s">%d</a>' % (num, options, num)
        else:
            return str(num)

    pages = range(max(page_obj.number - 3, 1),
                  min(page_obj.number + 3, page_obj.paginator.num_pages) + 1)
    links = '&nbsp;&nbsp;'.join(make_link(n, page_obj.number) for n in pages)

    if pages[0] != 1:
        links = '&hellip;&nbsp;' + links
    if pages[-1] != page_obj.paginator.num_pages:
        links += '&nbsp;&hellip;'

    return links

@register.filter
@stringfilter
def company_title(companies):
    """Format possibly multiple companies for the title"""
    if '\n' in companies:
        return companies.split('\n')[0] + ', et al'
    else:
        return companies

@register.filter
def foia_is_viewable(foia, user):
    """Make sure the FOIA is viewable before showing it to the user"""
    return foia.is_viewable(user)
