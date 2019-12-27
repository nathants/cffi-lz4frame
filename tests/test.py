import lz4frame
import os

def test_random(self):
    data = os.urandom(128 * 1024)  # Read 128kb
    assert data == lz4frame.decompress(lz4frame.compress(data))

def test_string(self):
    data = "test" * (5 * 1024 * 1024) # 5mb of string
    assert data == lz4frame.decompress(lz4frame.compress(data))
