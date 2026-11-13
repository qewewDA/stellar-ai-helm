"""Small, lossless-for-structure text Clausewitz parser.

Objects retain repeated keys and anonymous values instead of silently converting
the save format to a JSON object. No third-party parser code is used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import gc
import re
from typing import Any, Iterator


class PDXError(ValueError):
    """The input is not supported textual Clausewitz data."""


@contextmanager
def without_cyclic_gc():
    """Pause the cyclic collector while building large acyclic trees.

    A 19 MB dump allocates millions of tuples; repeated collector passes over
    the growing heap cost more than the parse itself. Reference counting still
    frees memory normally, and the previous collector state is restored.
    """
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


@dataclass
class PDXNode:
    entries: list[tuple[str | None, Any]] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the last matching key, preserving all matches in get_all()."""
        for entry_key, value in reversed(self.entries):
            if entry_key == str(key):
                return value
        return default

    def get_all(self, key: str) -> list[Any]:
        return [value for entry_key, value in self.entries if entry_key == str(key)]

    def items(self) -> Iterator[tuple[str, Any]]:
        return ((key, value) for key, value in self.entries if key is not None)

    def values(self) -> Iterator[Any]:
        return (value for key, value in self.entries if key is None)


@dataclass(frozen=True)
class _Token:
    text: str
    position: int
    quoted: bool = False


def _tokens(text: str) -> Iterator[_Token]:
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char.isspace() or char == '\ufeff':
            pos += 1
            continue
        if char == '#':
            newline = text.find('\n', pos)
            pos = len(text) if newline < 0 else newline + 1
            continue
        if char in '{}=':
            yield _Token(char, pos)
            pos += 1
            continue
        start = pos
        if char == '"':
            pos += 1
            value: list[str] = []
            while pos < len(text):
                char = text[pos]
                if char == '"':
                    pos += 1
                    yield _Token(''.join(value), start, True)
                    break
                if char == '\\':
                    pos += 1
                    if pos == len(text):
                        raise PDXError(f'Unterminated escape at offset {pos}')
                    escaped = text[pos]
                    value.append({'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}.get(escaped, '\\' + escaped))
                else:
                    value.append(char)
                pos += 1
            else:
                raise PDXError(f'Unterminated quoted string at offset {start}')
            continue
        while pos < len(text) and not text[pos].isspace() and text[pos] not in '{}=#"':
            pos += 1
        if start == pos:
            raise PDXError(f'Unexpected character at offset {pos}')
        yield _Token(text[start:pos], start)


_NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')


def _scalar(token: _Token) -> Any:
    if not token.quoted and _NUMBER.fullmatch(token.text):
        try:
            return int(token.text) if all(c not in token.text for c in '.eE') else float(token.text)
        except ValueError:
            pass
    return token.text


def _reference_loads(text: str) -> PDXNode:
    """Reference parser: parse text, raising PDXError for malformed input or excessive nesting.

    Keys always remain strings; numeric values become int/float. Dates and the
    PDX yes/no keywords remain strings. Typed color constructs such as ``rgb``
    are retained as separate anonymous values, not interpreted as commands.
    """
    if '\x00' in text:
        raise PDXError('Binary/NUL-containing saves are unsupported; use a text save')
    source = iter(_tokens(text))
    current = next(source, None)

    def take() -> _Token | None:
        nonlocal current
        result = current
        current = next(source, None)
        return result

    def node(depth: int, nested: bool) -> PDXNode:
        if depth > 128:
            raise PDXError('Object nesting exceeds the supported limit (128)')
        result = PDXNode()
        while current is not None:
            if current.text == '}' and not current.quoted:
                if not nested:
                    raise PDXError(f'Unexpected closing brace at offset {current.position}')
                take()
                return result
            if current.text == '=' and not current.quoted:
                raise PDXError(f'Unexpected equals sign at offset {current.position}')
            if current.text == '{' and not current.quoted:
                take()
                result.entries.append((None, node(depth + 1, True)))
                continue
            first = take()
            assert first is not None
            if current is not None and current.text == '=' and not current.quoted:
                take()
                if current is None or (current.text in '}=' and not current.quoted):
                    raise PDXError(f'Missing value for {first.text!r} at offset {first.position}')
                if current.text == '{' and not current.quoted:
                    take()
                    value = node(depth + 1, True)
                else:
                    token = take()
                    assert token is not None
                    value = _scalar(token)
                result.entries.append((first.text, value))
            else:
                result.entries.append((None, _scalar(first)))
        if nested:
            raise PDXError('Unterminated object: missing closing brace')
        return result

    return node(0, False)


# findall yields one string per token. Unmatched characters are exactly the
# whitespace and BOM characters the reference tokenizer skips: ``\s`` uses the
# same Unicode predicate as ``str.isspace()``, and every other character starts
# one of the alternatives. Comments start with '#', quoted strings with '"' and
# at least two characters; a lone '"' is an unterminated string.
_FAST_TOKEN = re.compile(
    r'#[^\n]*|[{}=]|"[^"\\]*(?:\\[\s\S][^"\\]*)*"|[^\s{}=#"\ufeff][^\s{}=#"]*|"')
_ESCAPE = re.compile(r'\\([\s\S])')
_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}


def _unescape(text: str) -> str:
    return _ESCAPE.sub(lambda match: _ESCAPES.get(match[1], match[0]), text) if '\\' in text else text


class _FastFallback(Exception):
    """The fast path met malformed input; the reference parser reports it."""


def _fast_loads(text: str) -> PDXNode:
    """Iterative equivalent of _reference_loads for well-formed input."""
    root = PDXNode()
    stack = [root.entries]
    append = root.entries.append
    pending = pending_key = None  # a word that may still become a key
    has_pending = False
    after_equals = False          # a key and '=' were read; a value must follow
    number = _NUMBER.fullmatch
    scalars: dict[str, Any] = {}  # unquoted word -> its deterministic scalar
    missing = scalars  # sentinel that is never a stored value
    for token in _FAST_TOKEN.findall(text):
        first = token[0]
        if first == '{':
            if after_equals:
                key = pending_key
                has_pending = after_equals = False
            else:
                if has_pending:
                    append((None, pending))
                    has_pending = False
                key = None
            if len(stack) > 128:
                raise _FastFallback
            child = PDXNode()
            append((key, child))
            stack.append(child.entries)
            append = child.entries.append
            continue
        if first == '}':
            if after_equals or len(stack) == 1:
                raise _FastFallback
            if has_pending:
                append((None, pending))
                has_pending = False
            stack.pop()
            append = stack[-1].append
            continue
        if first == '=':
            if not has_pending or after_equals:
                raise _FastFallback
            after_equals = True
            continue
        if first == '"':
            if len(token) == 1:
                raise _FastFallback
            key = value = _unescape(token[1:-1])
        elif first == '#':
            continue
        else:
            key = token
            value = scalars.get(token, missing)
            if value is missing:
                value = token
                if number(token):
                    try:
                        value = (int(token) if '.' not in token and 'e' not in token and 'E' not in token
                                 else float(token))
                    except ValueError:
                        pass
                scalars[token] = value
        if after_equals:
            append((pending_key, value))
            has_pending = after_equals = False
        else:
            if has_pending:
                append((None, pending))
            pending_key, pending, has_pending = key, value, True
    if after_equals or len(stack) != 1:
        raise _FastFallback
    if has_pending:
        append((None, pending))
    return root


def loads(text: str) -> PDXNode:
    """Parse text, raising PDXError for malformed input or excessive nesting.

    Keys always remain strings; numeric values become int/float. Dates and the
    PDX yes/no keywords remain strings. Typed color constructs such as ``rgb``
    are retained as separate anonymous values, not interpreted as commands.

    Well-formed input takes a regex-tokenized iterative path that builds the
    identical tree. Any malformed input is re-parsed by the reference parser,
    so every error type, message and offset is unchanged.
    """
    if '\x00' in text:
        raise PDXError('Binary/NUL-containing saves are unsupported; use a text save')
    with without_cyclic_gc():
        try:
            return _fast_loads(text)
        except _FastFallback:
            pass
        return _reference_loads(text)


# Braces, quoted strings, comments and lone quotes; everything else is skipped.
_BLOCK_TOKEN = re.compile(r'"[^"\\]*(?:\\[\s\S][^"\\]*)*"|#[^\n]*|[{}]|"')


def _block_end(text: str, position: int) -> int:
    """Return the offset after the brace closing a block opened before position."""
    depth = 1
    for match in _BLOCK_TOKEN.finditer(text, position):
        first = match[0][0]
        if first == '{':
            depth += 1
        elif first == '}':
            depth -= 1
            if depth == 0:
                return match.end()
        elif first == '"' and len(match[0]) == 1:
            raise PDXError(f'Unterminated quoted string at offset {match.start()}')
    raise PDXError('Unterminated object: missing closing brace')


def loads_selected(text: str, spec: dict) -> PDXNode:
    """Parse only the paths named by ``spec``; other objects become empty placeholders.

    ``spec`` maps a key to ``True`` (parse that object completely) or to a
    nested spec. At every selected level all scalar entries and the keys and
    order of all object entries are retained, so ``get``/``get_all`` on the
    selected paths return exactly what :func:`loads` returns. Skipped objects
    are only brace-balanced (quoted strings and comments honoured); grammar
    errors inside them are detected by the full parse that callers still run
    before trusting full state. Selected levels use the full grammar.
    """
    if '\x00' in text:
        raise PDXError('Binary/NUL-containing saves are unsupported; use a text save')
    with without_cyclic_gc():
        return _selected(text, 0, len(text), spec, nested=False)[0]


def _selected(text: str, position: int, limit: int, spec: dict, *, nested: bool) -> tuple[PDXNode, int]:
    result = PDXNode()
    append = result.entries.append
    pending_key = pending_value = None
    has_pending = after_equals = False
    search = _FAST_TOKEN.search
    while True:
        match = search(text, position, limit)
        if match is None:
            break
        token, position = match[0], match.end()
        first = token[0]
        if first == '{':
            if after_equals:
                key = pending_key
                has_pending = after_equals = False
            else:
                if has_pending:
                    append((None, pending_value))
                    has_pending = False
                key = None
            selection = spec.get(key) if key is not None else None
            if selection is True:
                end = _block_end(text, position)
                value = loads(text[position:end - 1])
                position = end
            elif isinstance(selection, dict):
                value, position = _selected(text, position, limit, selection, nested=True)
            else:
                value = PDXNode()
                position = _block_end(text, position)
            append((key, value))
            continue
        if first == '}':
            if after_equals or not nested:
                raise PDXError(f'Unexpected closing brace at offset {match.start()}')
            if has_pending:
                append((None, pending_value))
            return result, position
        if first == '=':
            if not has_pending or after_equals:
                raise PDXError(f'Unexpected equals sign at offset {match.start()}')
            after_equals = True
            continue
        if first == '#':
            continue
        if first == '"':
            if len(token) == 1:
                raise PDXError(f'Unterminated quoted string at offset {match.start()}')
            word, quoted = _unescape(token[1:-1]), True
        else:
            word, quoted = token, False
        if after_equals:
            append((pending_key, _fast_scalar(word, quoted)))
            has_pending = after_equals = False
        else:
            if has_pending:
                append((None, pending_value))
            pending_key, pending_value, has_pending = word, _fast_scalar(word, quoted), True
    if after_equals:
        raise PDXError('Missing value at end of input')
    if nested:
        raise PDXError('Unterminated object: missing closing brace')
    if has_pending:
        append((None, pending_value))
    return result, position


def _fast_scalar(text: str, quoted: bool) -> Any:
    if not quoted and _NUMBER.fullmatch(text):
        try:
            return int(text) if '.' not in text and 'e' not in text and 'E' not in text else float(text)
        except ValueError:
            pass
    return text
