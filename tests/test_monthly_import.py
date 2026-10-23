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


class CarouselCompletenessTests(unittest.TestCase):
    def test_two_photo_carousel_does_not_require_a_post_revisit(self):
        lead={'username':'shop','photo_posts':[]}
        item={'__typename':'GraphSidecar','shortcode':'pair','owner':{'username':'shop'},'timestamp':datetime.now(timezone.utc).timestamp(),'carousel_media_count':2,'carousel_media':[{'__typename':'GraphImage','display_url':'https://scontent.cdninstagram.com/'+str(i)+'.jpg'} for i in range(2)]}
        remember_photo(lead,item)
        self.assertEqual(len(lead['photo_posts'][0]['images']),2)
        self.assertTrue(lead['photo_posts'][0]['carousel_complete'])
        item['shortcode']='partial'
        item['carousel_media_count']=5
        remember_photo(lead,item)
        self.assertFalse(next(p for p in lead['photo_posts'] if p['code']=='partial')['carousel_complete'])

class RestrictionClassificationTests(unittest.TestCase):
    def test_only_content_failures_stop_collection(self):
        from store_finder.demo_browser import content_endpoint
        self.assertTrue(content_endpoint('https://www.instagram.com/graphql/query?secret=never-log'))
        self.assertTrue(content_endpoint('https://www.instagram.com/api/v1/feed/user/123/'))
        self.assertFalse(content_endpoint('https://www.instagram.com/api/v1/logging/client_event/'))
        self.assertFalse(content_endpoint('https://www.instagram.com/api/v1/notifications/badge/'))
        self.assertFalse(content_endpoint('https://evil.com/graphql/query'))
