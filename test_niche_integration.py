import unittest
import json
from app import app

class TestUnifiedStudioIntegration(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_01_index_html_contains_both_studios(self):
        print("\n[TEST 1] Testing Unified HTML Page...")
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        # Verify AutoShorts clipper components
        self.assertIn("AutoShorts AI", html)
        self.assertIn("viewClipper", html)
        self.assertIn("generateForm", html)
        self.assertIn("clipsGrid", html)
        # Verify Niche Finder components
        self.assertIn("viewNicheFinder", html)
        self.assertIn("subtab-niche", html)
        self.assertIn("subtab-channel", html)
        self.assertIn("subtab-hook", html)
        self.assertIn("subtab-ideas", html)
        # Verify the 1-click bridge function
        self.assertIn("sendVideoToClipper", html)
        print("-> Unified HTML serves both AutoShorts Clipper and Niche Studio with 1-click bridge.")

    def test_02_clipper_existing_endpoints(self):
        print("\n[TEST 2] Testing Existing Clipper Endpoints...")
        res_styles = self.client.get('/api/subtitle-styles')
        self.assertEqual(res_styles.status_code, 200)
        styles_data = res_styles.get_json()
        self.assertIn('styles', styles_data)
        self.assertIn('bold_pop', styles_data['styles'])

        res_clips = self.client.get('/api/clips')
        self.assertEqual(res_clips.status_code, 200)
        clips_data = res_clips.get_json()
        self.assertIn('clips', clips_data)
        print(f"-> Existing clipper endpoints intact. Current saved clips count: {len(clips_data['clips'])}")

    def test_03_niche_search_endpoint(self):
        print("\n[TEST 3] Testing /api/niche/search...")
        # Empty query validation
        r_empty = self.client.post('/api/niche/search', json={})
        self.assertEqual(r_empty.status_code, 400)

        # Valid query
        res = self.client.post('/api/niche/search', json={'query': 'Ancient Rome History'})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertGreater(len(data['channels']), 0)
        print(f"-> Found {len(data['channels'])} channels in niche. First: {data['channels'][0]['name']}")

    def test_04_niche_channel_deepdive_endpoint(self):
        print("\n[TEST 4] Testing /api/niche/channel...")
        # Empty input validation
        r_empty = self.client.post('/api/niche/channel', json={})
        self.assertEqual(r_empty.status_code, 400)

        # Deepdive analysis
        res = self.client.post('/api/niche/channel', json={'channel_input': 'veritasium', 'max_videos': 8})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        channel_data = data['data']
        self.assertIn('stats', channel_data)
        self.assertIn('median_views', channel_data['stats'])
        print(f"-> Channel analyzed: {channel_data['channel_name']}, Median Views: {channel_data['stats']['median_views']:,}")

    def test_05_niche_ideas_generator_endpoint(self):
        print("\n[TEST 5] Testing /api/niche/ideas...")
        res = self.client.post('/api/niche/ideas', json={
            'channel_name': 'Daily Stoic',
            'niche': 'Stoic Philosophy',
            'outlier_titles': ['The Stoic Rule for Inner Peace', 'How Marcus Aurelius Started His Day']
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['ideas']), 8)
        self.assertIn('title', data['ideas'][0])
        self.assertIn('thumbnail_concept', data['ideas'][0])
        self.assertIn('hook_script', data['ideas'][0])
        print(f"-> Generated {len(data['ideas'])} viral blueprints successfully. Concept #1: '{data['ideas'][0]['title']}'")

if __name__ == '__main__':
    unittest.main()
