from rvnode.devices.video import split_jpegs


def test_split_jpegs_across_chunks():
    a = b"\xff\xd8\xff\xe0" + b"A" * 10 + b"\xff\x00" + b"\xff\xd9"
    b = b"\xff\xd8\xff\xdb" + b"B" * 5 + b"\xff\xd9"
    buf = bytearray(b"\x00\x00" + a + b[:6])
    assert split_jpegs(buf) == [a]
    assert bytes(buf) == b[:6]          # 덜 온 프레임은 남긴다
    buf.extend(b[6:])
    assert split_jpegs(buf) == [b] and buf == bytearray()
    junk = bytearray(b"xyz" * 10)
    assert split_jpegs(junk) == [] and len(junk) <= 2
