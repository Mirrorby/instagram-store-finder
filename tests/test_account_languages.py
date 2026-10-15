import unittest
from unittest.mock import patch
from store_finder.account_languages import analyze_language, language_statistics, SUPPORTED
from store_finder.personal_demos import contact_draft, make_bundle, publish
from tests.test_personal_demos import FakeAPI

class AccountLanguageTests(unittest.TestCase):
    def test_search_is_an_estimate_and_never_sets_demo_language(self):
        lead={'evidence':[{'snippet':'Bijoux artisanaux fabriqués à Paris avec des matériaux de qualité.'}], 'query_language':'ru'}
        self.assertEqual(analyze_language(lead)['code'], 'fr')
        self.assertTrue(analyze_language(lead)['estimated'])
        self.assertEqual(analyze_language(lead, include_search=False)['code'], 'unknown')
        self.assertEqual(analyze_language({'query_language':'ru'})['code'], 'unknown')

    def test_counts_preserve_sources_and_unknowns(self):
        rows=[{'data':{'biography':'Handcrafted silver jewelry with worldwide shipping and beautiful gifts.'}},
              {'data':{'evidence':[{'snippet':'Bijoux artisanaux fabriqués à Paris avec des matériaux de qualité.'}]}},
              {'data':{'biography':'✨'}}]
        stats=language_statistics(rows)
        self.assertEqual((stats['total'],stats['profile_count'],stats['estimate_count'],stats['unknown']),(3,1,1,1))
        self.assertEqual(sum(l['total'] for l in stats['languages'])+stats['unknown'],stats['total'])

    def test_all_supported_languages_have_personalized_drafts(self):
        messages=[]
        for code in SUPPORTED:
            text=contact_draft({'store':{'name':'Test shop','settings':{'language':code}}},'https://demo.example/s/test')
            self.assertIn('Test shop',text)
            self.assertIn('https://demo.example/s/test',text)
            messages.append(text)
        self.assertEqual(len(set(messages)),len(SUPPORTED))

    def test_unsupported_server_is_rejected_before_creating_store(self):
        import tempfile
        from pathlib import Path
        api=FakeAPI()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'Обновите Delokay'):
                publish({'issues':[],'store':{'settings':{'language':'fr'}}},Path(tmp),api)
        self.assertEqual(api.creates,0)
