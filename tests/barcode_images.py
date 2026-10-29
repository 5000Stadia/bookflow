"""Barcode pictures for the camera tests (R190): EAN-13 drawn from its module pattern, as a PIL image, a PNG "photo",
or a small Y4M video for Chrome's fake camera (--use-file-for-fake-video-capture). Made at test time, so no binary
fixture lives in the repository."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

_L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011']
_R = [''.join('1' if bit == '0' else '0' for bit in code) for code in _L]
_G = [code[::-1] for code in _R]
_PARITY = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL']


def ean13_modules(code: str) -> str:
    """The 95 modules of an EAN-13 (1 = bar)."""
    assert len(code) == 13 and code.isdigit(), code
    parity = _PARITY[int(code[0])]
    bits = '101'
    for i, digit in enumerate(code[1:7]):
        bits += (_L if parity[i] == 'L' else _G)[int(digit)]
    bits += '01010'
    for digit in code[7:]:
        bits += _R[int(digit)]
    return bits + '101'


def ean13_image(code: str, size: tuple[int, int], *, module: int = 2) -> Image.Image:
    """The code centred on white, `module` pixels a module, bars half the height."""
    width, height = size
    image = Image.new('L', size, 255)
    draw = ImageDraw.Draw(image)
    modules = ean13_modules(code)
    left = (width - len(modules) * module) // 2
    assert left >= 9 * module, 'no quiet zone: make the picture wider or the modules smaller'
    top, bottom = height // 4, height * 3 // 4
    for i, bit in enumerate(modules):
        if bit == '1':
            draw.rectangle([left + i * module, top, left + (i + 1) * module - 1, bottom], fill=0)
    return image


def stacked(codes: tuple[str, ...], size: tuple[int, int]) -> Image.Image:
    """Several codes in one picture, one above another (two labels in one frame)."""
    width, height = size
    image = Image.new('L', size, 255)
    band = height // len(codes)
    for i, code in enumerate(codes):
        image.paste(ean13_image(code, (width, band)), (0, i * band))
    return image


def photo(path: Path, code: str) -> Path:
    """A PNG "photo" of one code, as a phone would hand the page."""
    image = Image.new('RGB', (800, 600), (236, 233, 226))
    image.paste(ean13_image(code, (480, 240), module=4).convert('RGB'), (160, 180))
    image.save(path, 'PNG')
    return path


def y4m(path: Path, segments: list[tuple[str | None, int]], *, size: tuple[int, int] = (320, 240), fps: int = 5) -> Path:
    """A grey 4:2:0 Y4M video: each segment is (a code, a tuple of codes shown together, or None for a blank frame,
    number of frames)."""
    width, height = size
    chroma = bytes([128]) * ((width // 2) * (height // 2) * 2)
    blank = Image.new('L', size, 255).tobytes()
    frames = {}
    with path.open('wb') as out:
        out.write(f'YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C420jpeg\n'.encode())
        for code, count in segments:
            if code not in frames:
                frames[code] = (stacked(code, size) if isinstance(code, tuple) else ean13_image(code, size)).tobytes() \
                    if code else blank
            for _ in range(count):
                out.write(b'FRAME\n' + frames[code] + chroma)
    return path
