import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from nicoview.settings import clean_settings, save_settings, load_settings
from nicoview.playback import build_player_args
from nicoview import svp


class SVPTests(unittest.TestCase):
    def test_opt_in_is_explicit_and_persisted(self):
        self.assertFalse(clean_settings({})['svp_enabled'])
        self.assertFalse(clean_settings({'svp_enabled':'false'})['svp_enabled'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'settings.json'
            save_settings(p,{'svp_enabled':True,'svp_player_path':'C:/SVP/mpv.exe'})
            self.assertTrue(load_settings(p)['svp_enabled'])
            self.assertEqual(load_settings(p)['svp_player_path'],'C:/SVP/mpv.exe')

    def test_missing_runtime_is_actionable(self):
        with patch.object(svp,'find_player',return_value=''):
            with self.assertRaisesRegex(ValueError,'SVP'):
                svp.player_path('')

    def test_runtime_requires_vapoursynth(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'mpv.exe';exe.touch()
            with self.assertRaisesRegex(ValueError,'VapourSynth'):
                svp.player_path(str(exe))
            (Path(d)/'VSScript.dll').touch()
            self.assertEqual(svp.player_path(str(exe)),str(exe))

    def test_svp_launch_does_not_load_scripts_that_replace_ipc(self):
        args=build_player_args('mpv.exe',svp.PIPE,'comments.lua',svp_enabled=True)
        self.assertIn('--load-scripts=no',args)
        self.assertIn('--hwdec=auto-copy',args)
        self.assertIn('--hr-seek-framedrop=no',args)
        self.assertIn('--input-ipc-server='+svp.PIPE,args)
        self.assertNotIn('--hwdec=auto-copy',build_player_args('mpv.exe','private','c.lua'))

    def test_only_known_python_environment_is_removed(self):
        with patch.dict('os.environ',{'PYTHONPATH':'other-runtime','PYTHONHOME':'other','PATH':'keep'}):
            env=svp.player_environment()
            self.assertNotIn('PYTHONPATH',env)
            self.assertNotIn('PYTHONHOME',env)
            self.assertEqual(env['PATH'],'keep')

    def test_server_check_uses_the_player_ipc_transport(self):
        ipc=Mock(spec=['server_process_id'])
        ipc.server_process_id.return_value=123
        svp.verify_server(ipc,123)
        ipc.server_process_id.assert_called_once_with()
        with self.assertRaisesRegex(ValueError,'SVP'):
            svp.verify_server(ipc,124)

    def test_closed_server_check_reports_an_actionable_svp_error(self):
        ipc=Mock(spec=['server_process_id'])
        ipc.server_process_id.side_effect=OSError('closed')
        with self.assertRaisesRegex(ValueError,'SVP'):
            svp.verify_server(ipc,123)
