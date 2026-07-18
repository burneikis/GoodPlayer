import numpy as np

from src.audio_engine import RingBuffer


def _data(n, channels=2, value=1.0):
    return np.full((n, channels), value, dtype=np.float32)


def make_buffer(seconds=1.0, rate=100, channels=2):
    return RingBuffer(seconds, rate, channels)


def test_write_and_read():
    buf = make_buffer()
    assert buf.write(_data(10)) == 10
    assert buf.available() == 10
    out = buf.read(10)
    assert out.shape == (10, 2)
    assert np.all(out == 1.0)
    assert buf.available() == 0


def test_partial_read_counts_underrun():
    buf = make_buffer()
    buf.write(_data(5))
    out = buf.read(10)
    assert len(out) == 5
    assert buf.underruns == 1


def test_read_empty_returns_empty():
    buf = make_buffer()
    out = buf.read(10)
    assert out.shape == (0, 2)


def test_wraparound():
    buf = make_buffer(seconds=1.0, rate=10)  # capacity 10
    buf.write(_data(8, value=1.0))
    buf.read(8)
    # Now write 6 samples: wraps around the end of the buffer
    buf.write(_data(6, value=2.0))
    out = buf.read(6)
    assert np.all(out == 2.0)


def test_overflow_drops_and_counts():
    buf = make_buffer(seconds=1.0, rate=10)  # capacity 10
    written = buf.write(_data(15))
    assert written == 10
    assert buf.dropped == 5
    assert buf.available() == 10


def test_write_full_buffer_returns_zero():
    buf = make_buffer(seconds=1.0, rate=10)
    buf.write(_data(10))
    assert buf.write(_data(5)) == 0
    assert buf.dropped == 5


def test_clear():
    buf = make_buffer()
    buf.write(_data(10))
    buf.clear()
    assert buf.available() == 0
    assert buf.space_available() == buf._capacity


def test_reset_underruns():
    buf = make_buffer()
    buf.read(5)
    assert buf.underruns == 1
    buf.reset_underruns()
    assert buf.underruns == 0


def test_data_order_preserved():
    buf = make_buffer(seconds=1.0, rate=100)
    a = np.arange(20, dtype=np.float32).reshape(10, 2)
    buf.write(a)
    out = buf.read(10)
    assert np.array_equal(out, a)
