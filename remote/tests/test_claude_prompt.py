import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('claude_prompt', Path(__file__).resolve().parents[1] / 'claude-auto.py')
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class CountingScreen(auto.pyte.Screen):
    def __init__(self, columns=100, lines=24):
        super().__init__(columns, lines)
        self.materializations = 0

    @property
    def display(self):
        self.materializations += 1
        return super().display


def composer(status_rows=3, hint='⏵⏵ auto mode on (shift+tab to cycle)', text='hello'):
    screen = CountingScreen()
    stream = auto.pyte.Stream(screen)
    stream.feed('\x1b[3;1H' + '─' * 100 + '\x1b[4;1H❯ ' + text + '\x1b[5;1H' + '─' * 100)
    for i in range(status_rows):
        stream.feed('\x1b[{};1Hcustom status {}'.format(6 + i, i))
    stream.feed('\x1b[{};1H{}\x1b[4;3H'.format(6 + status_rows, hint))
    return screen, stream


class PromptTests(unittest.TestCase):
    def test_wrapped_status_lines_do_not_hide_native_idle_hint(self):
        for status_rows in (0, 1, 3, 5, 7):
            screen, _ = composer(status_rows)
            self.assertEqual(auto.prompt_region(screen), (2, 4), status_rows)

    def test_gate_remains_bounded_and_requires_native_idle_evidence(self):
        screen, _ = composer(8)
        self.assertIsNone(auto.prompt_region(screen), 'Do not search arbitrary history')
        screen, _ = composer(3, hint='custom status only')
        self.assertIsNone(auto.prompt_region(screen))

    def test_working_hint_beyond_three_rows_blocks_handoff(self):
        screen, stream = composer(3)
        stream.feed('\x1b[10;1Hesc to interrupt\x1b[4;3H')
        self.assertIsNone(auto.prompt_region(screen))

    def test_hidden_caret_still_requires_composer_position(self):
        screen, stream = composer()
        stream.feed('\x1b[?25l')
        self.assertEqual(auto.prompt_region(screen), (2, 4))
        stream.feed('\x1b[2;3H')
        self.assertIsNone(auto.prompt_region(screen))

    def test_completion_mode_is_not_an_idle_prompt(self):
        screen, _ = composer(3, hint='❯ /model    Choose model', text='/mo')
        cache = auto.PromptSnapshot()
        self.assertIsNone(cache.detect(screen, 0))
        self.assertEqual(cache.detect(screen, 0, allow_completion=True), (2, 4))
        self.assertIsNone(cache.detect(screen, 0))
        self.assertEqual(screen.materializations, 1)

    def test_idle_polling_never_rebuilds_the_screen(self):
        screen, _ = composer()
        cache = auto.PromptSnapshot()
        for _ in range(1000):
            self.assertEqual(cache.detect(screen, 0), (2, 4))
        self.assertEqual(screen.materializations, 1)
        self.assertIs(cache.rows(screen, 0), cache.rows(screen, 0))

    def test_output_caret_and_resize_invalidate_correctly(self):
        screen, stream = composer()
        cache = auto.PromptSnapshot()
        self.assertEqual(cache.detect(screen, 0), (2, 4))
        screen.cursor.y = 1
        self.assertIsNone(cache.detect(screen, 0))
        self.assertEqual(screen.materializations, 1, 'Caret-only change needs no cell snapshot')
        screen.cursor.y = 3
        stream.feed('\x1b[9;1H\x1b[2Kesc to interrupt\x1b[4;3H')
        self.assertIsNone(cache.detect(screen, 1))
        self.assertEqual(screen.materializations, 2)
        screen.resize(26, 90)
        cache.detect(screen, 1)
        self.assertEqual(screen.materializations, 3)


if __name__ == '__main__':
    unittest.main()
