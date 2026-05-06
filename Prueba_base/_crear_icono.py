"""Genera icon.ico en la misma carpeta. Solo stdlib, sin dependencias extras."""
import struct, os

def create_ico(filename):
    W = H = 32
    BLUE  = bytes([159, 106, 45, 255])   # #2D6A9F en BGRA
    WHITE = bytes([255, 255, 255, 255])

    grid = [[BLUE] * W for _ in range(H)]

    def rect(y1, y2, x1, x2, color):
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                grid[y][x] = color

    # Gráfico de barras ascendente
    rect(14, 23,  4,  8, WHITE)
    rect(10, 23, 12, 16, WHITE)
    rect( 6, 23, 20, 24, WHITE)
    rect(24, 25,  4, 24, WHITE)   # línea base

    pixel_data = b''.join(
        b''.join(grid[y][x] for x in range(W))
        for y in range(H - 1, -1, -1)
    )
    and_mask = b'\x00' * (4 * H)

    bmp = struct.pack('<IiiHHIIiiII',
        40, W, H * 2, 1, 32, 0,
        len(pixel_data) + len(and_mask),
        0, 0, 0, 0
    )
    image = bmp + pixel_data + and_mask
    header = struct.pack('<HHH', 0, 1, 1)
    entry  = struct.pack('<BBBBHHII', W, H, 0, 0, 1, 32, len(image), 22)

    with open(filename, 'wb') as f:
        f.write(header + entry + image)

if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    create_ico(out)
    print(f'Icono creado: {out}')
