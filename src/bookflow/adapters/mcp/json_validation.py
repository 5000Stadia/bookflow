"""Incremental JSON syntax and result-shape validation without scalar assembly.

Memory is one decoded record, a grammar stack proportional to nesting depth,
three error-field markers and at most eight characters of the current token.
No row, full string, key, numeric lexeme or JSON path is materialized.
"""

import codecs
import re

_PLAIN = re.compile(r'[^"\\\x00-\x1f]+')
_SPACE = re.compile(r'[ \t\r\n]+')
_DIGITS = re.compile(r'[0-9]+')
_ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


class JsonDocumentValidator:
    def __init__(self):
        self.utf8 = codecs.getincrementaldecoder('utf-8')('strict')
        self.stack = []
        self.started = self.complete = False
        self.token = None
        self.capture = ''
        self.escape = False
        self.hex_left = self.hex_value = 0
        self.key = None
        self.count = 0
        self.error_fields = set()

    def _capture(self, value):
        if len(self.capture) < 8:
            self.capture += value[:8 - len(self.capture)]

    def _done(self, kind):
        if len(self.stack) == 1 and self.key is not None:
            if ((self.key == 'code' and kind == 'string' and self.capture.startswith('E_'))
                    or (self.key == 'message' and kind == 'string')
                    or (self.key == 'details' and kind == 'object')):
                self.error_fields.add(self.key)
            self.key = None

    def _start_value(self, char):
        if not self.started:
            if char != '{':
                raise ValueError('root object required')
            self.started = True
        elif self.stack:
            self.stack[-1] = 'object_end' if self.stack[-1] == 'object_value' else 'array_end'
        if char in '{[':
            self._done('object' if char == '{' else 'array')
            self.stack.append('object_first' if char == '{' else 'array_first')
        elif char == '"':
            self.token, self.capture = 'string', ''
        elif char in '-0123456789':
            self.token = 'number'
            self.number = 'minus' if char == '-' else 'zero' if char == '0' else 'integer'
            self._done('number')
        elif char in 'tfn':
            self.token, self.literal, self.literal_at = 'literal', {'t': 'true', 'f': 'false', 'n': 'null'}[char], 1
            self._done('literal')
        else:
            raise ValueError('value required')

    def _text(self, text):
        index = 0
        while index < len(text):
            char = text[index]
            if self.token in ('string', 'key'):
                if self.hex_left:
                    if char not in '0123456789abcdefABCDEF':
                        raise ValueError('invalid unicode escape')
                    self.hex_value = self.hex_value * 16 + int(char, 16)
                    self.hex_left -= 1
                    if not self.hex_left:
                        self._capture(chr(self.hex_value))
                elif self.escape:
                    self.escape = False
                    if char == 'u':
                        self.hex_left, self.hex_value = 4, 0
                    elif char in _ESCAPES:
                        self._capture(_ESCAPES[char])
                    else:
                        raise ValueError('invalid escape')
                elif char == '\\':
                    self.escape = True
                elif char == '"':
                    if self.token == 'key':
                        self.stack[-1] = 'colon'
                        if len(self.stack) == 1:
                            self.count += 1
                            self.key = self.capture
                    else:
                        self._done('string')
                    self.token = None
                elif ord(char) < 32:
                    raise ValueError('string control character')
                else:
                    match = _PLAIN.match(text, index)
                    # Slice only the bounded prefix needed for error-shape checks.
                    if len(self.capture) < 8:
                        self._capture(text[index:min(match.end(), index + 8)])
                    index = match.end()
                    continue
                index += 1
                continue
            if self.token == 'literal':
                if char != self.literal[self.literal_at]:
                    raise ValueError('invalid literal')
                self.literal_at += 1
                if self.literal_at == len(self.literal):
                    self.token = None
                index += 1
                continue
            if self.token == 'number':
                state = self.number
                if char in '0123456789':
                    if state == 'zero':
                        raise ValueError('leading zero')
                    if state == 'minus' and char == '0':
                        self.number = 'zero'
                        index += 1
                    else:
                        self.number = ('integer' if state in ('minus', 'integer') else
                                       'fraction' if state in ('dot', 'fraction') else 'exponent_digits')
                        index = _DIGITS.match(text, index).end()
                    continue
                if char == '.' and state in ('zero', 'integer'):
                    self.number = 'dot'
                elif char in 'eE' and state in ('zero', 'integer', 'fraction'):
                    self.number = 'exponent'
                elif char in '+-' and state == 'exponent':
                    self.number = 'exponent_sign'
                else:
                    if state not in ('zero', 'integer', 'fraction', 'exponent_digits'):
                        raise ValueError('incomplete number')
                    self.token = None
                    continue  # The delimiter belongs to the enclosing grammar.
                index += 1
                continue
            if char in ' \t\r\n':
                index = _SPACE.match(text, index).end()
                continue
            if self.complete:
                raise ValueError('trailing content')
            state = self.stack[-1] if self.stack else 'root'
            if state in ('object_first', 'object_key'):
                if char == '}' and state == 'object_first':
                    self.stack.pop()
                elif char == '"':
                    self.token, self.capture = 'key', ''
                else:
                    raise ValueError('object key required')
            elif state == 'colon':
                if char != ':':
                    raise ValueError('colon required')
                self.stack[-1] = 'object_value'
            elif state in ('object_end', 'array_end'):
                if char == ',':
                    self.stack[-1] = 'object_key' if state == 'object_end' else 'array_value'
                elif char == ('}' if state == 'object_end' else ']'):
                    self.stack.pop()
                else:
                    raise ValueError('container separator required')
            elif state == 'array_first' and char == ']':
                self.stack.pop()
            else:
                self._start_value(char)
            if self.started and not self.stack:
                self.complete = True
            index += 1

    def feed(self, chunk):
        from .framing import invalid
        try:
            self._text(self.utf8.decode(chunk, final=False))
        except (ValueError, UnicodeError):
            raise invalid('invalid_json') from None

    def finish(self, is_error):
        from .framing import invalid
        try:
            self._text(self.utf8.decode(b'', final=True))
        except (ValueError, UnicodeError):
            raise invalid('invalid_json') from None
        if not self.complete or self.token is not None or self.stack:
            raise invalid('invalid_json')
        error_document = self.count == 3 and self.error_fields == {'code', 'message', 'details'}
        if error_document != is_error:
            raise invalid('invalid_business_completion')
