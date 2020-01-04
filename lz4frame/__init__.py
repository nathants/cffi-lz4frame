from cffi import FFI
import io
import os
import itertools

ffibuilder = FFI()

_path = os.path.dirname(__file__)
_lz4_files = ['lz4.c', 'lz4hc.c', 'lz4frame.c', 'xxhash.c']
_sources = [os.path.relpath(os.path.join(_path, p)) for p in _lz4_files]

ffibuilder.cdef(r"""

extern "Python" size_t _py_fread(char* ptr, size_t size, size_t count, unsigned stream);
extern "Python" size_t _py_fwrite(char* ptr, size_t size, size_t count, unsigned stream);

typedef struct {
    int error;
    unsigned long long size_in;
    unsigned long long size_out;
} compressResult_t;

static int decompress_file(unsigned f_in, unsigned f_out);
static compressResult_t compress_file(unsigned f_in, unsigned f_out);


""")

ffibuilder.set_source(
    "_lz4frame_cffi",
    r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <assert.h>

#include <lz4frame.h>

#define IN_CHUNK_SIZE  (1024*1024)

static size_t _py_fread(char* ptr, size_t size, size_t count, unsigned stream);
static size_t _py_fwrite(char* ptr, size_t size, size_t count, unsigned stream);

static const LZ4F_preferences_t kPrefs = {
    { LZ4F_max256KB, LZ4F_blockLinked, LZ4F_noContentChecksum, LZ4F_frame,
      0 /* unknown content size */, 0 /* no dictID */ , LZ4F_noBlockChecksum },
    0,   /* compression level; 0 == default */
    0,   /* autoflush */
    0,   /* favor decompression speed */
    { 0, 0, 0 },  /* reserved, must be set to 0 */
};

typedef struct {
    int error;
    unsigned long long size_in;
    unsigned long long size_out;
} compressResult_t;

static size_t get_block_size(const LZ4F_frameInfo_t* info) {
    switch (info->blockSizeID) {
        case LZ4F_default:
        case LZ4F_max64KB:  return 1 << 16;
        case LZ4F_max256KB: return 1 << 18;
        case LZ4F_max1MB:   return 1 << 20;
        case LZ4F_max4MB:   return 1 << 22;
        default:
            printf("Impossible with expected frame specification (<=v1.6.1)\n");
            exit(1);
    }
}

/* @return : 1==error, 0==success */
static int decompress_file_internal(unsigned f_in, unsigned f_out, LZ4F_dctx* dctx, void* src, size_t srcCapacity, size_t filled, size_t alreadyConsumed, void* dst, size_t dstCapacity) {
    int firstChunk = 1;
    size_t ret = 1;
    assert(f_in != NULL); assert(f_out != NULL);
    assert(dctx != NULL);
    assert(src != NULL); assert(srcCapacity > 0); assert(filled <= srcCapacity); assert(alreadyConsumed <= filled);
    assert(dst != NULL); assert(dstCapacity > 0);
    /* Decompression */
    while (ret != 0) {
        /* Load more input */
        size_t readSize = firstChunk ? filled : _py_fread(src, 1, srcCapacity, f_in); firstChunk=0;
        const void* srcPtr = (const char*)src + alreadyConsumed; alreadyConsumed=0;
        const void* const srcEnd = (const char*)srcPtr + readSize;
        if (readSize == 0) {
            printf("Decompress: not enough input or error reading file\n");
            return 1;
        }
        /* Decompress:
         * Continue while there is more input to read (srcPtr != srcEnd)
         * and the frame isn't over (ret != 0)
         */
        while (srcPtr < srcEnd && ret != 0) {
            /* Any data within dst has been flushed at this stage */
            size_t dstSize = dstCapacity;
            size_t srcSize = (const char*)srcEnd - (const char*)srcPtr;
            ret = LZ4F_decompress(dctx, dst, &dstSize, srcPtr, &srcSize, /* LZ4F_decompressOptions_t */ NULL);
            if (LZ4F_isError(ret)) {
                printf("Decompression error: %s\n", LZ4F_getErrorName(ret));
                return 1;
            }
            /* Flush output */
            if (dstSize != 0) _py_fwrite(dst, 1, dstSize, f_out);
            /* Update input */
            srcPtr = (const char*)srcPtr + srcSize;
        }
        assert(srcPtr <= srcEnd);
        /* Ensure all input data has been consumed.
         * It is valid to have multiple frames in the same file,
         * but this example only supports one frame.
         */
        if (srcPtr < srcEnd) {
            printf("Decompress: Trailing data left in file after frame\n");
            return 1;
        }
    }
    /* Check that there isn't trailing data in the file after the frame.
     * It is valid to have multiple frames in the same file,
     * but this example only supports one frame.
     */
    {   size_t const readSize = _py_fread(src, 1, 1, f_in);
        if (readSize != 0) {
            printf("Decompress: Trailing data left in file after frame\n");
            return 1;
    }   }
    return 0;
}

/* @return : 1==error, 0==completed */
static int decompress_file_allocDst(unsigned f_in, unsigned f_out, LZ4F_dctx* dctx, void* src, size_t srcCapacity) {
    // assert(f_in != NULL); assert(f_out != NULL);
    assert(dctx != NULL);
    assert(src != NULL);
    assert(srcCapacity >= LZ4F_HEADER_SIZE_MAX);  /* ensure LZ4F_getFrameInfo() can read enough data */
    /* Read Frame header */
    size_t const readSize = _py_fread(src, 1, srcCapacity, f_in);
    if (readSize == 0) {
        printf("Decompress: not enough input or error reading file\n");
        return 1;
    }
    LZ4F_frameInfo_t info;
    size_t consumedSize = readSize;
    {   size_t const fires = LZ4F_getFrameInfo(dctx, &info, src, &consumedSize);
        if (LZ4F_isError(fires)) {
            printf("LZ4F_getFrameInfo error: %s\n", LZ4F_getErrorName(fires));
            return 1;
    }   }
    /* Allocating enough space for an entire block isn't necessary for
     * correctness, but it allows some memcpy's to be elided.
     */
    size_t const dstCapacity = get_block_size(&info);
    void* const dst = malloc(dstCapacity);
    if (!dst) { perror("decompress_file(dst)"); return 1; }
    int const decompressionResult = decompress_file_internal(f_in, f_out, dctx, src, srcCapacity, readSize-consumedSize, consumedSize, dst, dstCapacity);
    free(dst);
    return decompressionResult;
}

/* @result : 1==error, 0==success */
static int decompress_file(unsigned f_in, unsigned f_out) {
    // assert(f_in != NULL); assert(f_out != NULL);
    /* Ressource allocation */
    void* const src = malloc(IN_CHUNK_SIZE);
    if (!src) { perror("decompress_file(src)"); return 1; }
    LZ4F_dctx* dctx;
    {   size_t const dctxStatus = LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION);
        if (LZ4F_isError(dctxStatus)) {
            printf("LZ4F_dctx creation error: %s\n", LZ4F_getErrorName(dctxStatus));
    }   }
    int const result = !dctx ? 1 /* error */ : decompress_file_allocDst(f_in, f_out, dctx, src, IN_CHUNK_SIZE);
    free(src);
    LZ4F_freeDecompressionContext(dctx);   /* note : free works on NULL */
    return result;
}

static compressResult_t compress_file_internal(unsigned f_in, unsigned f_out, LZ4F_compressionContext_t ctx, void* inBuff,  size_t inChunkSize, void* outBuff, size_t outCapacity) {
    compressResult_t result = { 1, 0, 0 };  /* result for an error */
    unsigned long long count_in = 0, count_out;
    assert(f_in != NULL); assert(f_out != NULL);
    assert(ctx != NULL);
    assert(outCapacity >= LZ4F_HEADER_SIZE_MAX);
    assert(outCapacity >= LZ4F_compressBound(inChunkSize, &kPrefs));
    /* write frame header */
    {   size_t const headerSize = LZ4F_compressBegin(ctx, outBuff, outCapacity, &kPrefs);
        if (LZ4F_isError(headerSize)) {
            printf("Failed to start compression: error %u \n", (unsigned)headerSize);
            return result;
        }
        count_out = headerSize;
        // printf("Buffer size is %u bytes, header size %u bytes \n", (unsigned)outCapacity, (unsigned)headerSize);
        _py_fwrite(outBuff, 1, headerSize, f_out);
    }
    /* stream file */
    for (;;) {
        size_t const readSize = _py_fread(inBuff, 1, IN_CHUNK_SIZE, f_in);
        if (readSize == 0) break; /* nothing left to read from input file */
        count_in += readSize;
        size_t const compressedSize = LZ4F_compressUpdate(ctx,
                                                outBuff, outCapacity,
                                                inBuff, readSize,
                                                NULL);
        if (LZ4F_isError(compressedSize)) {
            printf("Compression failed: error %u \n", (unsigned)compressedSize);
            return result;
        }
        // printf("Writing %u bytes\n", (unsigned)compressedSize);
        _py_fwrite(outBuff, 1, compressedSize, f_out);
        count_out += compressedSize;
    }
    /* flush whatever remains within internal buffers */
    {   size_t const compressedSize = LZ4F_compressEnd(ctx,
                                                outBuff, outCapacity,
                                                NULL);
        if (LZ4F_isError(compressedSize)) {
            printf("Failed to end compression: error %u \n", (unsigned)compressedSize);
            return result;
        }
        // printf("Writing %u bytes \n", (unsigned)compressedSize);
        _py_fwrite(outBuff, 1, compressedSize, f_out);
        count_out += compressedSize;
    }
    result.size_in = count_in;
    result.size_out = count_out;
    result.error = 0;
    return result;
}

static compressResult_t compress_file(unsigned f_in, unsigned f_out) {
    assert(f_in != NULL);
    assert(f_out != NULL);
    /* ressource allocation */
    LZ4F_compressionContext_t ctx;
    size_t const ctxCreation = LZ4F_createCompressionContext(&ctx, LZ4F_VERSION);
    void* const src = malloc(IN_CHUNK_SIZE);
    size_t const outbufCapacity = LZ4F_compressBound(IN_CHUNK_SIZE, &kPrefs);   /* large enough for any input <= IN_CHUNK_SIZE */
    void* const outbuff = malloc(outbufCapacity);
    compressResult_t result = { 1, 0, 0 };  /* == error (default) */
    if (!LZ4F_isError(ctxCreation) && src && outbuff) {
        result = compress_file_internal(f_in, f_out,
                                        ctx,
                                        src, IN_CHUNK_SIZE,
                                        outbuff, outbufCapacity);
    } else {
        printf("error : ressource allocation failed \n");
    }
    LZ4F_freeCompressionContext(ctx);   /* supports free on NULL */
    free(src);
    free(outbuff);
    return result;
}

""",
    sources=_sources,
    extra_compile_args=['-Wall', '-O3',  '-march=native', '-mtune=native'])

try:
    from _lz4frame_cffi import ffi, lib
except:
    pass
else:
    read_i = itertools.count(0)
    read_streams = {}

    @ffi.def_extern()
    def _py_fread(ptr, size, count, stream):
        size *= count
        val = read_streams[stream].read(size)
        read_size = len(val)
        ffi.memmove(ptr, val, read_size)
        return read_size

    write_i = itertools.count(0)
    write_streams = {}

    @ffi.def_extern()
    def _py_fwrite(ptr, size, count, stream):
        size *= count
        val = ffi.buffer(ptr, size)
        write_size = write_streams[stream].write(val)
        return write_size

    def decompress(some_bytes):
        in_file = next(read_i)
        read_streams[in_file] = io.BytesIO(some_bytes)
        out_file = next(write_i)
        write_streams[out_file] = io.BytesIO()
        try:
            assert 0 == lib.decompress_file(in_file, out_file)
            return write_streams[out_file].getvalue()
        finally:
            del read_streams[in_file]
            del write_streams[out_file]

    def compress(some_bytes):
        in_file = next(read_i)
        read_streams[in_file] = io.BytesIO(some_bytes)
        out_file = next(write_i)
        write_streams[out_file] = io.BytesIO()
        try:
            result = lib.compress_file(in_file, out_file)
            assert result.error == 0
            return write_streams[out_file].getvalue()
        finally:
            del read_streams[in_file]
            del write_streams[out_file]

if __name__ == '__main__':
    ffibuilder.compile(verbose=True)
