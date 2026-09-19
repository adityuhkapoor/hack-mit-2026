import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageChops
import server

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = patch.object(server, 'STORE', Path(self.temp.name))
        self.store.start()
        image = Image.new('RGB', (1280, 720), '#ca9357')
        buf = io.BytesIO(); image.save(buf, 'JPEG')
        self.camera = patch.object(server.CAMERA, 'snapshot', return_value=buf.getvalue())
        self.camera.start()
        server.SENSORS.clear()

    def tearDown(self):
        self.camera.stop(); self.store.stop(); self.temp.cleanup(); server.SENSORS.clear()

    def test_capture_rotation_and_immutable_original(self):
        job = server.capture({'rotation':90})
        self.assertEqual((job['width'], job['height']), (720, 1280))
        original = (server.STORE/job['id']/'original.jpg').read_bytes()
        for mode in ['enhance','noir','dream','sports','food']:
            result = server.render({'id':job['id'],'mode':mode,'strength':0.8})
            self.assertEqual(result['provider'],'local')
            self.assertTrue((server.ROOT/result['url'].lstrip('/')).suffix == '.jpg')
        self.assertEqual(original, (server.STORE/job['id']/'original.jpg').read_bytes())

    def test_stale_sensors_are_not_used(self):
        server.SENSORS.update(source='arduino',received=time.monotonic()-4,values={'light':0.2})
        s = server.sensor_snapshot()
        self.assertFalse(s['fresh']); self.assertEqual(s['values'], {})
        job=server.capture({})
        self.assertFalse(job['sensors']['fresh'])

    def test_sensor_snapshot_frozen_and_simulation_labeled(self):
        server.SENSORS.update(source='simulation',received=time.monotonic(),values={'temperature_c':35})
        job=server.capture({})
        server.SENSORS['values']['temperature_c']=10
        self.assertEqual(job['sensors']['values']['temperature_c'],35)
        self.assertEqual(job['sensors']['source'],'simulation')

    def test_bad_values_and_traversal_rejected(self):
        for value in [float('nan'), float('inf'), True, '42', 1.1]:
            with self.assertRaises(ValueError):
                server.validate_sensors({'source':'arduino','values':{'light':value}})
        for value in ['../.env', '/etc/passwd', None]:
            with self.assertRaises(ValueError):server.job_dir(value)
        job=server.capture({})
        with self.assertRaises(ValueError):server.render({'id':job['id'],'strength':float('nan')})

    def test_missing_keys_dont_destroy_capture(self):
        job=server.capture({})
        with patch.dict(server.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError,'not configured'):
                server.render({'id':job['id'],'mode':'ai','prompt':'Paper world'})
            with self.assertRaisesRegex(ValueError,'not configured'):
                server.speech({'id':job['id'],'text':'Hello'})
        self.assertTrue((server.STORE/job['id']/'original.jpg').exists())

    def test_twenty_captures_are_unique(self):
        jobs=[server.capture({}) for _ in range(20)]
        self.assertEqual(len({j['id'] for j in jobs}),20)
        self.assertEqual(len(list(server.STORE.glob('*/manifest.json'))),20)

    def test_zero_strength_preserves_image(self):
        im=Image.new('RGB',(50,40),'#c98a51')
        for mode in ['enhance','dream','noir']:
            out=server.local_effect(im,mode,0,{'fresh':False,'values':{}})
            self.assertIsNone(ImageChops.difference(im,out).getbbox())

if __name__ == '__main__':unittest.main()
