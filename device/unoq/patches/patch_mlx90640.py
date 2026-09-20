"""Patch Adafruit_MLX90640 for the UNO Q (Zephyr core). Idempotent; run on the board:

    python3 patch_mlx90640.py ~/Arduino/libraries/Adafruit_MLX90640/Adafruit_MLX90640.cpp

The core's Wire ignores the stop bit, so each address write ends in a STOP and the MLX90640 answers from
the wrong address (every pixel NaN). Read with Zephyr's i2c_write_read(), which does the repeated start.
The sketch names the bus with mlx_set_zephyr_i2c(); until it does, reads go through Wire as before.
"""
import sys

HEAD = '''
#if defined(__ZEPHYR__)
#include <zephyr/drivers/i2c.h>
static const struct device *nimbus_i2c = NULL;
static uint8_t nimbus_addr = 0x33;
void mlx_set_zephyr_i2c(const struct device *dev, uint8_t addr) { nimbus_i2c = dev; nimbus_addr = addr; }
#endif
'''
READ = '''
#if defined(__ZEPHYR__)
  if (nimbus_i2c) {
    cmd[0] = startAddress >> 8;
    cmd[1] = startAddress & 0x00FF;
    if (i2c_write_read(nimbus_i2c, nimbus_addr, cmd, 2, (uint8_t *)data, nMemAddressRead * 2) != 0)
      return -1;
    for (int i = 0; i < nMemAddressRead; i++)
      data[i] = __builtin_bswap16(data[i]);
    return 0;
  }
#endif
'''
path = sys.argv[1]
s = open(path).read()
if "nimbus_i2c" in s:
    print("already patched")
    sys.exit()
anchor = "#include <Adafruit_MLX90640.h>\n"
assert anchor in s
s = s.replace(anchor, anchor + HEAD, 1)
anchor = "  uint8_t cmd[2];\n\n  while (nMemAddressRead > 0) {"
assert anchor in s
s = s.replace(anchor, "  uint8_t cmd[2];\n" + READ + "\n  while (nMemAddressRead > 0) {", 1)
open(path, "w").write(s)
print("patched")
