import lz4
import os

def test_random(self):
    data = os.urandom(128 * 1024)  # Read 128kb
    assert data == lz4.decompress(lz4.compress(data))

def test_string(self):
    data = "test" * (5 * 1024 * 1024) # 5mb of string
    assert data == lz4.decompress(lz4.compress(data))
