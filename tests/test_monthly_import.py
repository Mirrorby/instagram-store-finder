import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from store_finder.railway_service import normalize
from store_finder.demo_media import remember_photo
from store_finder.site_copy import product_copy, store_copy

class MonthlyImportTests(unittest.TestCase):
    def test_period_request_is_not_a_six_post_limit(self):
        request=normalize({'instagram':'shop','periodDays':30})
        self.assertEqual(request['period_days'],30)
        self.assertEqual(request['products'],500)
        for value in [True,0,91,'30']:
            with self.assertRaises(ValueError):
                normalize({'instagram':'shop','periodDays':value})

    def test_carousels_keep_eight_distinct_photos_and_ignore_video(self):
        lead={'username':'shop','photo_posts':[],'photo_post_limit':501,'period_days':30}
        children=[{'__typename':'GraphImage','display_url':'https://scontent.cdninstagram.com/'+str(i)+'.jpg'} for i in range(10)]
        children.append({'__typename':'GraphVideo','display_url':'https://scontent.cdninstagram.com/video.jpg'})
        for n in range(70):
            remember_photo(lead,{'__typename':'GraphSidecar','shortcode':'post'+str(n),'owner':{'username':'shop'},'taken_at_timestamp':datetime.now(timezone.utc).timestamp(),'edge_sidecar_to_children':{'edges':[{'node':c} for c in children]} })
        self.assertEqual(len(lead['photo_posts']),70)
        self.assertEqual(len(lead['photo_posts'][0]['images']),8)
        self.assertTrue(all('video' not in x for x in lead['photo_posts'][0]['images']))

    def test_copy_adapts_facts_and_removes_promotion(self):
        caption='Silver flower earrings\nMaterial: sterling silver\nLength: 3 cm\nDM to order\n#jewelry https://example.com'
        title,category,body=product_copy(caption,'Shop','en')
        self.assertEqual(category,'Earrings')
        self.assertIn('sterling silver',body)
        self.assertNotIn('#',body)
        self.assertNotIn('DM',body)
        self.assertNotEqual(body,caption)
        self.assertNotIn('free shipping',body.lower())
        self.assertIn('Shop',store_copy('Shop',[{'category':category}],'en'))

if __name__=='__main__':
    unittest.main()
