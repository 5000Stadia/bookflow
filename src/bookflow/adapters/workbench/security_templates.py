"""Nonce trusted template source, never user-supplied rendered markup."""
import re
from jinja2 import FileSystemLoader


class NonceLoader(FileSystemLoader):
    def get_source(self, environment, template):
        source, filename, uptodate = super().get_source(environment, template)
        source = re.sub(r'<script(?=[\s>])',
                        '<script{% if request is defined and request.state.csp_nonce is defined %} nonce="{{ request.state.csp_nonce }}"{% endif %}', source)
        return source, filename, uptodate
