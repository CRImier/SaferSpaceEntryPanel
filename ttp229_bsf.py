# MicroPython ESP8266 driver for TTP229-BSF 16-key capacitive keypad in serial interface mode
# by Alan Wang

import utime

class Keypad:

    def __init__(self, scl, sdo, inputs=8, multi=False, raw=False):
        self._scl_pin = scl
        self._sdo_pin = sdo
        self._inputs = inputs
        self._multi_mode = multi
        self._raw_mode = raw

    def read(self):
        key = [1] * self._inputs
        self._scl_pin.on()
        utime.sleep_ms(1)
        for i in range(self._inputs):
            self._scl_pin.off()
            utime.sleep_ms(1)
            key[i] = self._sdo_pin.value()
            self._scl_pin.on()
            utime.sleep_ms(1)
        utime.sleep_ms(1)
        if self._raw_mode:
            return tuple(key)
        else:
            if self._multi_mode:
                key_multi = []
                for i in range(self._inputs):
                    if key[i] == 0:
                        key_multi.append(i)
                return tuple(key_multi)
            else:
                key_single = -1
                for i in range(self._inputs):
                    if key[i] == 0:
                        key_single = i
                        break
                return key_single
