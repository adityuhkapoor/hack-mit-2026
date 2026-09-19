import subprocess
import unittest
from unittest.mock import patch
from arduino_link import ArduinoLink


class BoardTests(unittest.TestCase):
    @patch('arduino_link.shutil.which', return_value='/usr/bin/adb')
    @patch('arduino_link.subprocess.run')
    def test_verified_board_and_led(self, run, which):
        run.side_effect = [subprocess.CompletedProcess([], 0, 'List of devices attached\nabc\tdevice\n'),
                           subprocess.CompletedProcess([], 0, 'UNO_Q\n'),
                           subprocess.CompletedProcess([], 0)]
        board = ArduinoLink()
        board.check()
        self.assertTrue(board.status()['connected'])
        board.pulse()
        self.assertEqual(board.status()['capture_feedback'], 'LED write verified')
        self.assertFalse(board.status()['physical_shutter'])

    @patch('arduino_link.shutil.which', return_value='/usr/bin/adb')
    @patch('arduino_link.subprocess.run', side_effect=OSError('unplugged'))
    def test_disconnect(self, run, which):
        board = ArduinoLink()
        board.serial = 'old'
        board.check()
        self.assertFalse(board.status()['connected'])

    def test_stale_board(self):
        board = ArduinoLink()
        board.serial = 'old'
        self.assertFalse(board.status()['connected'])


if __name__ == '__main__':
    unittest.main()
