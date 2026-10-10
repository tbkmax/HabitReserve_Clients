import unittest
from unittest.mock import Mock

from time_bank_tracker import hotkeys


class HotkeyTests(unittest.TestCase):
    def test_registration_and_cleanup_use_the_main_window_handle(self):
        user32 = Mock()
        user32.RegisterHotKey.return_value = 1
        user32.UnregisterHotKey.return_value = 1
        manager = hotkeys.WindowsHotkeys(1234, user32=user32)
        manager.register("Ctrl+Alt+W", "Ctrl+Alt+P")
        manager.clear()

        self.assertEqual(user32.RegisterHotKey.call_count, 2)
        self.assertEqual(user32.UnregisterHotKey.call_count, 2)
        self.assertEqual(user32.RegisterHotKey.call_args_list[0].args[0].value, 1234)
        self.assertEqual(user32.UnregisterHotKey.call_args_list[0].args[0].value, 1234)


if __name__ == "__main__":
    unittest.main()
