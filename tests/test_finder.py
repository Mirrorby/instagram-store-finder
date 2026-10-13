import json
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from store_finder.models import merge_result, profile_username
from store_finder.queries import load_queries, select_queries
from store_finder.runner import discover
from store_finder.scoring import score
from store_finder.search import SearchUnavailable
from store_finder.storage import empty_state, read_state, safe_cell, write_state, export_state
from store_finder.sheets import HEADERS, sheet_row, lead_from_row, plan_updates


QUERY = {"query": 'site:instagram.com jewelry shop "(@"', "language": "en", "niche": "jewelry"}


def row(username="example_shop", body="Handmade jewelry shop. DM to order."):
    return {"href": f"https://www.instagram.com/{username}/", "title": "Example", "body": body}


class FinderTests(unittest.TestCase):
    def test_url_canonicalization(self):
        self.assertEqual(profile_username("https://www.instagram.com/EXAMPLE_shop/?hl=ru"), "example_shop")
        self.assertEqual(profile_username("https://m.instagram.com/example.shop/reels/"), "example.shop")
        self.assertEqual(profile_username("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.instagram.com%2Fexample_shop%2F"), "example_shop")
        for invalid in ["https://www.instagram.com/p/abc/", "https://www.instagram.com/reel/abc/",
                        "https://www.instagram.com/accounts/login/", "https://instagram.com.evil.test/store/",
                        "https://evilinstagram.com/store/", "https://www.instagram.com/store/other/",
                        "https://www.instagram.com/.bad/", "https://www.instagram.com/bad../"]:
            self.assertIsNone(profile_username(invalid), invalid)

    def test_merge_preserves_review_and_removes_duplicates(self):
        leads = {}
        self.assertTrue(merge_result(leads, row(), QUERY, "2026-01-01"))
        leads["example_shop"]["review_status"] = "contacted"
        self.assertFalse(merge_result(leads, row(), QUERY, "2026-01-02"))
        lead = leads["example_shop"]
        self.assertEqual(len(lead["evidence"]), 1)
        self.assertEqual(lead["review_status"], "contacted")
        self.assertEqual(lead["first_seen"], "2026-01-01")
        self.assertEqual(lead["last_seen"], "2026-01-02")

    def test_store_signals_in_multiple_languages(self):
        for snippet in ["Jewelry shop. DM to order.", "Магазин украшений. Заказы в директ.",
                        "Tienda de joyas. Pedidos por DM.", "Loja de roupas. Entrega.",
                        "متجر مجوهرات للطلب", "Takı mağaza sipariş", "アクセサリー 販売"]:
            leads = {}
            merge_result(leads, row(body=snippet), QUERY, "today")
            lead = score(leads["example_shop"])
            self.assertEqual(lead["classification"], "likely_store", snippet)
            self.assertIsNone(lead["followers"])
            self.assertEqual(lead["website_status"], "unknown")

    def test_queries_do_not_count_as_evidence(self):
        leads = {}
        merge_result(leads, row(body="Personal account, travel and food."), QUERY, "today")
        self.assertEqual(score(leads["example_shop"])["score"], 0)
        self.assertEqual(leads["example_shop"]["classification"], "needs_review")

    def test_service_and_substring_false_positives(self):
        for snippet in ["Jewelry shop marketing agency. Website design.", "Fantastic photoshop tutorials"]:
            leads = {}
            merge_result(leads, row(body=snippet), QUERY, "today")
            self.assertEqual(score(leads["example_shop"])["classification"], "needs_review")

    def test_language_rotation_and_wrap(self):
        queries = load_queries(Path("config/queries.json"))
        batch, cursor = select_queries(queries, 0, 14)
        self.assertEqual(len({q["language"] for q in batch}), 14)
        self.assertEqual(cursor, 14)
        wrapped, cursor = select_queries(queries, len(queries) - 1, 2)
        self.assertEqual(cursor, 1)
        self.assertEqual(wrapped[1], queries[0])

    def test_persistence_is_encrypted_and_wrong_key_is_rejected(self):
        state = empty_state()
        merge_result(state["leads"], row(), QUERY, "2026-10-12T18:00:00+00:00")
        score(state["leads"]["example_shop"])
        key = Fernet.generate_key().decode()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "leads.fernet"
            write_state(path, state, key)
            self.assertNotIn(b"example_shop", path.read_bytes())
            self.assertEqual(read_state(path, key), state)
            with self.assertRaises(ValueError):
                read_state(path, Fernet.generate_key().decode())
            export_state(path, key, Path(temp) / "export")
            self.assertIn("example_shop", (Path(temp) / "export/leads.csv").read_text())

    def test_spreadsheet_formula_escaping(self):
        for malicious in ["=IMPORTXML('url')", "+1+2", "@SUM(1)", " \t=1"]:
            self.assertTrue(safe_cell(malicious).startswith("'"))
        self.assertEqual(safe_cell("Shop name"), "Shop name")

    def test_sheets_upsert_preserves_manual_columns_and_first_seen(self):
        leads = {}
        merge_result(leads, row(), QUERY, "2026-10-12T18:00:00+00:00")
        score(leads["example_shop"])
        old = sheet_row(leads["example_shop"])
        old[12] = 12345
        old[16:] = ["Написал", "contact", "demo", "note"]
        updates, additions = plan_updates([old], leads)
        self.assertFalse(additions)
        self.assertEqual(updates[0]["range"], "A2:P2")
        self.assertEqual(len(updates[0]["values"][0]), 16)
        self.assertEqual(updates[0]["values"][0][12], 12345)
        self.assertEqual(old[16:], ["Написал", "contact", "demo", "note"])

    def test_sheets_roundtrip_dates_and_russian_headers(self):
        leads = {}
        merge_result(leads, row(), QUERY, "2026-10-12T18:00:00+00:00")
        score(leads["example_shop"])
        values = sheet_row(leads["example_shop"])
        self.assertEqual(len(values), len(HEADERS))
        restored = lead_from_row(values)
        self.assertEqual(restored["first_seen"], "2026-10-12T18:00:00+00:00")
        self.assertEqual(restored["niche_hints"], ["jewelry"])
        self.assertEqual(restored["query_languages"], ["en"])

    def test_sheets_rich_link_title_uses_stable_account(self):
        leads = {}
        merge_result(leads, row(), QUERY, "2026-10-12T18:00:00+00:00")
        values = sheet_row(leads["example_shop"])
        values[1] = "Example Shop (@example_shop) • Instagram photos and videos"
        restored = lead_from_row(values)
        self.assertEqual(restored["profile_url"], "https://www.instagram.com/example_shop/")
        values[1] = "https://www.instagram.com/different_shop/"
        with self.assertRaises(ValueError):
            lead_from_row(values)

    def test_concatenated_search_cards_are_not_other_shop_evidence(self):
        leads = {}
        combined = row(body='12 Followers, 2 Posts - Personal (@example_shop): "My life".100 Followers, 3 Posts - Other (@other): "Jewelry shop. DM to order."')
        combined["title"] = "Personal (@example_shop) - InstagramOther Jewelry Shop (@other) - Instagram"
        merge_result(leads, combined, QUERY, "today")
        lead = score(leads["example_shop"])
        self.assertEqual(lead["score"], 0)
        self.assertNotIn("Jewelry", lead["title"])

    def test_partial_outage_and_repeat_run(self):
        class Client:
            def search(self, query, limit, page=1):
                if query == "offline":
                    raise SearchUnavailable("bing:TimeoutException")
                return [row(), row(), {"href": "https://instagram.com/reel/123/"}], "bing", []
        queries = [QUERY, {**QUERY, "query": "offline"}]
        state = empty_state()
        stats = discover(state, queries, Client(), 2, 10, 0)
        self.assertEqual(stats["new_profiles"], 1)
        self.assertEqual(stats["likely_stores"], 1)
        self.assertEqual(stats["queries_succeeded"], 1)
        self.assertEqual(len(state["leads"]), 1)
        stats = discover(state, queries, Client(), 2, 10, 0)
        self.assertEqual(stats["new_profiles"], 0)
        self.assertEqual(len(state["leads"]), 1)

    def test_complete_outage_keeps_cursor_and_leads(self):
        class Client:
            def search(self, query, limit, page=1):
                raise SearchUnavailable("bing:TimeoutException")
        state = empty_state()
        merge_result(state["leads"], row(), QUERY, "yesterday")
        stats = discover(state, [QUERY, QUERY], Client(), 1, 10, 0)
        self.assertEqual(stats["queries_succeeded"], 0)
        self.assertEqual(state["cursor"], 0)
        self.assertEqual(len(state["leads"]), 1)


if __name__ == "__main__":
    unittest.main()
