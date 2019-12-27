import lz4frame
import random
import os

def test_random():
    for _ in range(100):
        print('.', end='', flush=True)
        data = os.urandom(int(random.random() * int(10 * 1024 * 1024))) # Read 1mb
        with open('/tmp/test.data', 'wb') as f:
            f.write(data)
        assert data == lz4frame.decompress(lz4frame.compress(data)), 'failed with test data: /tmp/tests.data'

def test_string():
    data = b'test' * (1024 * 1024) # 40mb of string
    assert data == lz4frame.decompress(lz4frame.compress(data))
