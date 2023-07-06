#creating speed benchmark tests for encryption and non encryption
from __future__ import annotations
import os
from typing import Any
from cryptography.fernet import Fernet
from proxystore.connectors.endpoint import EndpointConnector
import sys

# olist = []
# for i in range(10):
#     olist.append(os.urandom(10))

# endpoint = EndpointConnector(["5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a"])
# crypt = Fernet.generate_key()

# def encrypted(objects):
#     keys = endpoint.put_batch(objects)
#     ret = endpoint.get_batch(keys)
#     return ret



# def no_encrypt(objects, crypt):
#     keys = endpoint.put_batch(objects, True, crypt)
#     ret = endpoint.get_batch(keys, True, crypt)
#     return ret



if __name__=="__main__":
    sizelist = int(sys.argv[1])
    sizebyte = int(sys.argv[2])
    bool  = sys.argv[3] == "True"
    olist = []
    for i in range(sizelist):
        olist.append(os.urandom(sizebyte))

    endpoint = EndpointConnector(["5ea36b1d-b1f0-4d7f-9c7b-74d86df4cf1a"])
    crypt = Fernet.generate_key()

    keys = endpoint.put_batch(olist, bool, crypt)
    objects = endpoint.get_batch(keys, bool, crypt)

    assert(olist == objects)

