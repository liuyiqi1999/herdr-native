import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from native_tab_group import split_group

class NativeTabGroupsTest(unittest.TestCase):
    def test_single_tab_creates_left_shell_without_moving_any_process(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def call(*args):
                calls.append(args)
                if args[:2]==('tab','list'):return {'tabs':[{'tab_id':'w1:t1'}]}
                if args[:2]==('tab','create'):return {'tab':{'tab_id':'w1:t2'}}
                self.fail(args)
            args=SimpleNamespace(root=root,boot='boot',group='main',tab='w1:t1',workspace='w1',previous='')
            launched=[]
            group=split_group(args,call,launched.append)
            self.assertEqual(launched,[group])
            self.assertEqual((Path(root)/'boot/w1:t1').read_text(),group)
            self.assertEqual((Path(root)/'boot/w1:t2').read_text(),'main')
            self.assertIn('--no-focus',calls[1])
            self.assertEqual((Path(root)/'focus-main').read_text(),'w1:t2')

    def test_failed_native_split_restores_source_ownership(self):
        with tempfile.TemporaryDirectory() as root:
            args=SimpleNamespace(root=root,boot='boot',group='main',tab='w1:t2',workspace='w1',previous='w1:t1')
            def call(*args):return {'tabs':[{'tab_id':'w1:t1'},{'tab_id':'w1:t2'}]}
            def fail(group):raise RuntimeError('scripting denied')
            with self.assertRaisesRegex(RuntimeError,'scripting denied'):
                split_group(args,call,fail)
            self.assertFalse((Path(root)/'boot/w1:t2').exists())

    def test_repeat_on_already_moved_tab_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            owner=Path(root)/'boot/w1:t2';owner.parent.mkdir();owner.write_text('right')
            args=SimpleNamespace(root=root,boot='boot',group='main',tab='w1:t2',workspace='w1',previous='w1:t1')
            with self.assertRaisesRegex(RuntimeError,'already moved'):
                split_group(args,lambda *args:self.fail('must not call API'),lambda group:self.fail('must not launch'))
            self.assertEqual(owner.read_text(),'right')

    def test_drop_transfers_identity_without_launching_or_restarting(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'surface-right').write_text('surface-uuid')
            args = SimpleNamespace(root=root, boot='boot', group='main', tab='w1:t2',
                                   workspace='w1', previous='w1:t1', target_group='right')
            calls = []
            def call(*command):
                calls.append(command)
                return {'tabs': [{'tab_id': 'w1:t1'}, {'tab_id': 'w1:t2'}]}
            focused = []
            group = split_group(args, call, lambda _: self.fail('must not launch'), focused.append)
            self.assertEqual(focused, ['right'])
            self.assertEqual(group, 'right')
            self.assertEqual((Path(root) / 'boot/w1:t2').read_text(), 'right')
            self.assertEqual((Path(root) / 'focus-right').read_text(), 'w1:t2')
            self.assertEqual(len(calls), 1)

    def test_closed_native_surface_rolls_back_drop(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'surface-right').write_text('closed-surface')
            args = SimpleNamespace(root=root, boot='boot', group='main', tab='w1:t2',
                                   workspace='w1', previous='w1:t1', target_group='right')
            def fail_focus(_): raise RuntimeError('surface closed')
            with self.assertRaisesRegex(RuntimeError, 'surface closed'):
                split_group(args, lambda *args: {'tabs': [{'tab_id': 'w1:t2'}]},
                            lambda _: self.fail('must not launch'), fail_focus)
            self.assertFalse((Path(root) / 'boot/w1:t2').exists())
            self.assertFalse((Path(root) / 'focus-right').exists())

    def test_missing_destination_cannot_take_ownership(self):
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(root=root, boot='boot', group='main', tab='w1:t2',
                                   workspace='w1', previous='', target_group='closed')
            with self.assertRaisesRegex(RuntimeError, 'no longer available'):
                split_group(args, lambda *args: {'tabs': [{'tab_id': 'w1:t2'}]},
                            lambda _: self.fail('must not launch'))
            self.assertFalse((Path(root) / 'boot/w1:t2').exists())

    def test_failed_split_restores_existing_focus_requests(self):
        with tempfile.TemporaryDirectory() as root:
            focus = Path(root) / 'focus-main'
            focus.write_text('old-focus')
            args = SimpleNamespace(root=root, boot='boot', group='main', tab='w1:t2',
                                   workspace='w1', previous='w1:t1')
            def fail(_): raise RuntimeError('denied')
            with self.assertRaises(RuntimeError):
                split_group(args, lambda *args: {'tabs': [{'tab_id': 'w1:t2'}]}, fail)
            self.assertEqual(focus.read_text(), 'old-focus')
            self.assertEqual(list(Path(root).glob('focus-*')), [focus])

if __name__=='__main__':unittest.main()
