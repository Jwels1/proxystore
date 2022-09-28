"""Globus Auth Unit Tests."""
from __future__ import annotations

import contextlib
import json
import os
from unittest import mock

import globus_sdk
import pytest

from proxystore.globus import _TOKENS_FILE
from proxystore.globus import authenticate
from proxystore.globus import get_authorizer
from proxystore.globus import get_proxystore_authorizer
from proxystore.globus import GlobusAuthFileError
from proxystore.globus import load_tokens_from_file
from proxystore.globus import main
from proxystore.globus import proxystore_authenticate
from proxystore.globus import save_tokens_to_file


def test_save_load_tokens(tmp_dir: str) -> None:
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_file = os.path.join(tmp_dir, 'globus.json')
    data = {'tokens': {'token': '123456789'}}
    with mock.patch('globus_sdk.OAuthTokenResponse'):
        tokens = globus_sdk.OAuthTokenResponse()
        tokens.by_resource_server = data  # type: ignore

    save_tokens_to_file(tmp_file, tokens)
    assert load_tokens_from_file(tmp_file) == data


def test_authenticate(capsys) -> None:
    # This test is heavily mocked so most just checks for simple errors
    with mock.patch('globus_sdk.NativeAppAuthClient'), mock.patch(
        'builtins.input',
        return_value='123456789',
    ), contextlib.redirect_stdout(
        None,
    ):
        authenticate('1234', 'https://redirect')


def test_get_authorizer(tmp_dir: str) -> None:
    tokens = {
        'transfer.api.globus.org': {
            'refresh_token': 1234,
            'access_token': 1234,
            'expires_at_seconds': 1234,
        },
    }
    os.makedirs(tmp_dir, exist_ok=True)
    filepath = os.path.join(tmp_dir, 'tokens.json')
    with open(filepath, 'w') as f:
        json.dump(tokens, f)

    with mock.patch('globus_sdk.NativeAppAuthClient'), mock.patch(
        'globus_sdk.RefreshTokenAuthorizer',
    ):
        get_authorizer('client id', filepath, 'redirect uri')


def test_get_authorizer_missing_file(tmp_dir: str) -> None:
    filepath = os.path.join(tmp_dir, 'missing_file')
    with pytest.raises(GlobusAuthFileError):
        get_authorizer('client id', filepath, 'redirect uri')


def test_proxystore_authenticate(tmp_dir: str) -> None:
    data = {'tokens': {'token': '123456789'}}
    with mock.patch('globus_sdk.OAuthTokenResponse'):
        tokens = globus_sdk.OAuthTokenResponse()
        tokens.by_resource_server = data  # type: ignore

    with mock.patch('proxystore.globus.authenticate', return_value=tokens):
        proxystore_authenticate(tmp_dir)

    assert load_tokens_from_file(os.path.join(tmp_dir, _TOKENS_FILE)) == data

    with mock.patch('proxystore.globus.get_authorizer'):
        get_proxystore_authorizer(tmp_dir)


def test_main(tmp_dir: str) -> None:
    with mock.patch('proxystore.globus.proxystore_authenticate'), mock.patch(
        'proxystore.globus.get_proxystore_authorizer',
        side_effect=[GlobusAuthFileError(), None, None],
    ), contextlib.redirect_stdout(None):
        # First will raise auth file missing error and trigger auth flow
        main()
        # Second will find auth file and just exit
        main()