from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet


from proxystore.connectors.endpoint import EndpointConnector


endpoint = EndpointConnector(["5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a"])

def single_encrypt(estr, object, bool, ckey, ckey2):

    endpoint = EndpointConnector([estr])

    key = endpoint.put(object, bool, ckey)

    ret = endpoint.get(key, bool, ckey2)
    return ret

def single_noencrypt(estr, object):

    endpoint = EndpointConnector([estr])

    key = endpoint.put(object)

    ret = endpoint.get(key)
    return ret

def batch_encrypt(estr, object_list, bool, ckey, ckey2):

    endpoint = EndpointConnector([estr])
    keys = endpoint.put_batch(object_list, bool, ckey)

    ret = endpoint.get_batch(keys, bool, ckey2)
    return ret
    
def batch_noencrypt(estr, object_list):

    endpoint = EndpointConnector([estr])
    keys = endpoint.put_batch(object_list)

    ret = endpoint.get_batch(keys)
    return ret

crypt = Fernet.generate_key()
def test_one():
    assert single_encrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", b'what could this ever possibly be', True, crypt, crypt) == b'what could this ever possibly be'

crypt2 = Fernet.generate_key()
#def test_two():
 #   assert single("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a",b'what could this ever possibly be', True, crypt, crypt2) == b'what could this ever possibly be'

def test_three():
    assert single_encrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", b'what could this ever possibly be', False, crypt, crypt) == b'what could this ever possibly be'

def test_four():
    assert single_noencrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", b'what could this ever possibly be') == b'what could this ever possibly be'


olist = [b'hello', b'world', b'final', b'end' ]


def test_five():
    assert batch_encrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", olist, True, crypt, crypt) == olist

def test_six():
    assert batch_encrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", olist, False, crypt, crypt2) == olist

def test_seven():
    assert batch_noencrypt("5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a", olist) == olist