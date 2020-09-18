#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPLv3 Copyright: 2020, Kovid Goyal <kovid at kovidgoyal.net>


import os
import posixpath
from contextlib import suppress
from functools import lru_cache
from typing import (
    Any, Generator, Iterable, List, NamedTuple, Optional, Tuple, cast
)
from urllib.parse import ParseResult, unquote, urlparse

from .conf.utils import to_bool, to_cmdline
from .config import KeyAction, parse_key_action
from .constants import config_dir
from .typing import MatchType
from .utils import expandvars, log_error


class MatchCriteria(NamedTuple):
    type: MatchType
    value: str


class OpenAction(NamedTuple):
    match_criteria: Tuple[MatchCriteria, ...]
    actions: Tuple[KeyAction, ...]


def parse(lines: Iterable[str]) -> Generator[OpenAction, None, None]:
    match_criteria: List[MatchCriteria] = []
    actions: List[KeyAction] = []

    for line in lines:
        line = line.strip()
        if line.startswith('#'):
            continue
        if not line:
            if match_criteria and actions:
                yield OpenAction(tuple(match_criteria), tuple(actions))
            match_criteria = []
            actions = []
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        key, rest = parts
        key = key.lower()
        if key == 'action':
            with to_cmdline.filter_env_vars('URL', 'FILE_PATH', 'FILE', 'FRAGMENT'):
                x = parse_key_action(rest)
            if x is not None:
                actions.append(x)
        elif key in ('mime', 'ext', 'protocol', 'file', 'path', 'url', 'has_fragment'):
            if key != 'url':
                rest = rest.lower()
            match_criteria.append(MatchCriteria(cast(MatchType, key), rest))
        else:
            log_error(f'Ignoring malformed open actions line: {line}')

    if match_criteria and actions:
        yield OpenAction(tuple(match_criteria), tuple(actions))


def url_matches_criterion(purl: 'ParseResult', url: str, mc: MatchCriteria) -> bool:
    if mc.type == 'url':
        import re
        try:
            pat = re.compile(mc.value)
        except re.error:
            return False
        return pat.search(url) is not None

    if mc.type == 'mime':
        import fnmatch
        from mimetypes import guess_type
        try:
            mt = guess_type(purl.path)[0]
        except Exception:
            return False
        if mt is None:
            return False
        mt = mt.lower()
        for mpat in mc.value.split(','):
            mpat = mpat.strip()
            with suppress(Exception):
                if fnmatch.fnmatchcase(mt, mpat):
                    return True
        return False

    if mc.type == 'ext':
        if not purl.path:
            return False
        path = purl.path.lower()
        for ext in mc.value.split(','):
            ext = ext.strip()
            if path.endswith('.' + ext):
                return True
        return False

    if mc.type == 'protocol':
        protocol = (purl.scheme or 'file').lower()
        for key in mc.value.split(','):
            if key.strip() == protocol:
                return True
        return False

    if mc.type == 'has_fragment':
        return to_bool(mc.value) == bool(purl.fragment)

    if mc.type == 'path':
        import fnmatch
        try:
            return fnmatch.fnmatchcase(purl.path.lower(), mc.value)
        except Exception:
            return False

    if mc.type == 'file':
        import fnmatch
        import posixpath
        try:
            fname = posixpath.basename(purl.path)
        except Exception:
            return False
        try:
            return fnmatch.fnmatchcase(fname.lower(), mc.value)
        except Exception:
            return False


def url_matches_criteria(purl: 'ParseResult', url: str, criteria: Iterable[MatchCriteria]) -> bool:
    for x in criteria:
        try:
            if not url_matches_criterion(purl, url, x):
                return False
        except Exception:
            return False
    return True


def actions_for_url_from_list(url: str, actions: Iterable[OpenAction]) -> Generator[KeyAction, None, None]:
    try:
        purl = urlparse(url)
    except Exception:
        return
    path = unquote(purl.path)

    env = {
        'URL': url,
        'FILE_PATH': path,
        'FILE': posixpath.basename(path),
        'FRAGMENT': purl.fragment
    }

    def expand(x: Any) -> Any:
        if isinstance(x, str):
            return expandvars(x, env, fallback_to_os_env=False)
        return x

    for action in actions:
        if url_matches_criteria(purl, url, action.match_criteria):
            for ac in action.actions:
                yield ac._replace(args=tuple(map(expand, ac.args)))
            return


@lru_cache(maxsize=2)
def load_open_actions() -> Tuple[OpenAction, ...]:
    try:
        f = open(os.path.join(config_dir, 'open-actions.conf'))
    except FileNotFoundError:
        return ()
    with f:
        return tuple(parse(f))


def actions_for_url(url: str, actions_spec: Optional[str] = None) -> Generator[KeyAction, None, None]:
    if actions_spec is None:
        actions = load_open_actions()
    else:
        actions = tuple(parse(actions_spec.splitlines()))
    yield from actions_for_url_from_list(url, actions)
