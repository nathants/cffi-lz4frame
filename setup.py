from setuptools import setup

setup(
    name='cffi-lz4frame',
    version='0.0.0',
    author='nathants',
    author_email='me@nathants.com',
    url='http://github.com/nathants/cffi-lz4frame/',
    packages=['lz4frame'],
    install_requires=['cffi>=1.0.0'],
    cffi_modules=["lz4frame/__init__.py:ffibuilder"],
    setup_requires=['cffi>=1.0.0'],
)
