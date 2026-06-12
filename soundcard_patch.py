"""Workaround for soundcard 0.4.5 failing on non-extensible WASAPI mix formats.

soundcard's _AudioClient.__init__ asserts that GetMixFormat returns a
WAVEFORMATEXTENSIBLE (wFormatTag == 0xFFFE) float32 format and bails out
with a bare AssertionError otherwise. Some drivers (e.g. Jabra Link 380
after a mid-2026 driver/Windows update) report a plain WAVEFORMATEX
instead, which made every mic open fail.

Since the stream is opened in shared mode with the AUTOCONVERTPCM and
SRC_DEFAULT_QUALITY flags, WASAPI accepts a self-built extensible float32
format regardless of what the device reports — so when the device format
is not the expected extensible float32, we construct one ourselves.

Note: soundcard's cffi WAVEFORMATEXTENSIBLE cdef is not packed like the
real Windows struct (offsets shift by 2/4 bytes after WAVEFORMATEX), which
is why the fallback builds the packed byte layout manually with struct
instead of using the cdef fields — and why the float-GUID check below uses
soundcard's odd "empirical" misaligned field values.

Importing this module applies the patch (Windows only, no-op elsewhere).
Written against soundcard 0.4.5.
"""

import collections.abc
import struct
import sys

if sys.platform == 'win32':
    from soundcard import mediafoundation as _mf

    # KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, 00000003-0000-0010-8000-00aa00389b71
    _FLOAT_SUBFORMAT = bytes((
        0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00,
        0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71,
    ))

    def _packed_float_format(samplerate: int, channels: int) -> bytes:
        """Packed WAVEFORMATEXTENSIBLE (40 bytes) for float32 audio."""
        return struct.pack(
            '<HHIIHHHHI16s',
            0xFFFE,                       # wFormatTag = WAVE_FORMAT_EXTENSIBLE
            channels,                     # nChannels
            int(samplerate),              # nSamplesPerSec
            int(samplerate) * channels * 4,  # nAvgBytesPerSec
            channels * 4,                 # nBlockAlign
            32,                           # wBitsPerSample
            22,                           # cbSize
            32,                           # Samples.wValidBitsPerSample
            (1 << channels) - 1,          # dwChannelMask
            _FLOAT_SUBFORMAT,             # SubFormat
        )

    def _is_extensible_float(mix) -> bool:
        # The Data1-4 values are soundcard's misaligned reads of the real
        # float GUID; they match what the unpatched asserts expected.
        return (
            mix.Format.wFormatTag == 0xFFFE
            and mix.Format.cbSize == 22
            and mix.SubFormat.Data1 == 0x100000
            and mix.SubFormat.Data2 == 0x0080
            and mix.SubFormat.Data3 == 0xaa00
            and [int(x) for x in mix.SubFormat.Data4[0:4]] == [0, 56, 155, 113]
        )

    def _patched_init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
        _ffi, _com, _ole32 = _mf._ffi, _mf._com, _mf._ole32
        self._ptr = ptr

        if isinstance(channels, int):
            self.channelmap = list(range(channels))
        elif isinstance(channels, collections.abc.Iterable):
            self.channelmap = channels
        else:
            raise TypeError('channels must be iterable or integer')

        if list(range(len(set(self.channelmap)))) != sorted(list(set(self.channelmap))):
            raise TypeError('Due to limitations of WASAPI, channel maps on Windows '
                            'must be a combination of `range(0, x)`.')

        if blocksize is None:
            blocksize = self.deviceperiod[0] * samplerate

        ppMixFormat = _ffi.new('WAVEFORMATEXTENSIBLE**')
        hr = self._ptr[0][0].lpVtbl.GetMixFormat(self._ptr[0], ppMixFormat)
        _com.check_error(hr)

        channel_count = len(set(self.channelmap))
        manual_format = None
        if _is_extensible_float(ppMixFormat[0][0]):
            # Original soundcard path: adjust the device format in place
            ppMixFormat[0][0].Format.nChannels = channel_count
            ppMixFormat[0][0].Format.nSamplesPerSec = int(samplerate)
            ppMixFormat[0][0].Format.nAvgBytesPerSec = int(samplerate) * channel_count * 4
            ppMixFormat[0][0].Format.nBlockAlign = channel_count * 4
            ppMixFormat[0][0].Format.wBitsPerSample = 32
            ppMixFormat[0][0].Samples = dict(wValidBitsPerSample=32)
            pFormat = ppMixFormat[0]
        else:
            manual_format = _ffi.new('char[]', _packed_float_format(samplerate, channel_count))
            pFormat = _ffi.cast('WAVEFORMATEXTENSIBLE*', manual_format)

        if exclusive_mode:
            sharemode = _ole32.AUDCLNT_SHAREMODE_EXCLUSIVE
        else:
            sharemode = _ole32.AUDCLNT_SHAREMODE_SHARED
        #             resample   | remix      | better-SRC | nopersist
        streamflags = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000
        if isloopback:
            streamflags |= 0x00020000  # loopback
        bufferduration = int(blocksize / samplerate * 10000000)  # in hecto-nanoseconds
        hr = self._ptr[0][0].lpVtbl.Initialize(self._ptr[0], sharemode, streamflags,
                                               bufferduration, 0, pFormat, _ffi.NULL)
        _com.check_error(hr)
        _ole32.CoTaskMemFree(ppMixFormat[0])
        del manual_format  # keep the cffi buffer alive until after Initialize

        self.samplerate = samplerate
        self._idle_start_time = None

    _mf._AudioClient.__init__ = _patched_init
