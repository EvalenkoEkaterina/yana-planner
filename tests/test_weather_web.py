import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yana_planner.web import create_app


class WeatherWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app({'TESTING': True,
            'DATABASE': str(Path(self.tmp.name) / 'runs.sqlite3'),
            'WEATHER_RESULTS': str(Path(self.tmp.name) / 'missing.json')})
        self.client = self.app.test_client()

    def test_real_calculation_persists_and_refresh_does_not_run_worker(self):
        response = self.client.post('/weather/calculate', data={'threshold': '10'})
        self.assertEqual(response.status_code, 303)
        url = response.headers['Location']
        with patch('yana_planner.weather_web.subprocess.run', side_effect=AssertionError):
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn('Требуется вмешательство диспетчера', page.text)
            self.assertIn('action="/weather"', page.text)
            download = self.client.get(url + '/result.json')
            data = download.get_json()
            self.assertEqual(len(data['event']['closures']), 9)
            self.assertEqual(set(data['runs']), {'ready', 'milp'})
            self.assertIn('attachment', download.headers['Content-Disposition'])
            self.assertIn(url, self.client.get('/weather').text)
        # Reopen with a fresh application to verify durable storage.
        other = create_app(dict(self.app.config)).test_client()
        self.assertEqual(other.get(url + '/result.json').get_json(), data)

    def test_missing_snapshot_invalid_input_and_timeout(self):
        self.assertEqual(self.client.get('/weather').status_code, 200)
        self.assertEqual(self.client.get('/weather?threshold=bad').status_code, 400)
        self.assertEqual(self.client.post('/weather/calculate', data={'threshold':'999'}).status_code, 400)
        self.assertEqual(self.client.get('/weather/runs/9999').status_code, 404)
        with patch('yana_planner.weather_web.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('worker', 30)):
            self.assertEqual(self.client.post('/weather/calculate', data={'threshold':'10'}).status_code, 408)
