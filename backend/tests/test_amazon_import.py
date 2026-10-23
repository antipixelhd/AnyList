import csv
import io
import unittest
from zipfile import ZipFile
from core.amazon_import import parse_amazon_zip, _identity


def archive(rows, summaries=()):
    result = io.BytesIO()
    with ZipFile(result, "w") as z:
        for name, data, fields in [
            ("Viewing History.csv", rows, ["Title", "Seconds Viewed", "Playback Start Datetime (UTC)", "Material Type Description", "Is Deleted", "Preferred Language Code"]),
            ("Watch Events.csv", summaries, ["Title Name", "Seconds Watched", "Most Recent Watch Date", "Deleted from Watch History"])]:
            text = io.StringIO(); writer = csv.DictWriter(text, fieldnames=fields)
            writer.writeheader(); writer.writerows(data)
            z.writestr("activity/" + name, text.getvalue())
    return result.getvalue()


def session(title="Episode-Show - Season 2", seconds=1800, **extra):
    return {"Title": title, "Seconds Viewed": str(seconds),
            "Playback Start Datetime (UTC)": "2022-01-01T12:00:00Z",
            "Material Type Description": "Feature", **extra}


class AmazonImportTests(unittest.TestCase):
    def test_filters_brief_playback_trailers_deleted_and_nonfinite(self):
        rows = [session(), session(seconds=599), session(seconds="NaN"),
                session(title="Official Trailer", seconds=900),
                session(**{"Is Deleted": "Yes"}),
                session(**{"Material Type Description": "Trailer"})]
        parsed = parse_amazon_zip(archive(rows))
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.excluded_rows, 5)
        self.assertEqual(parsed.rows[0].show_title, "Show")

    def test_summary_corroboration_does_not_duplicate_watch(self):
        summary = {"Title Name": "Episode", "Seconds Watched": "1800",
                   "Most Recent Watch Date": "2022-01-01T13:00:00Z"}
        parsed = parse_amazon_zip(archive([session(), session()], [summary]))
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.duplicate_rows, 2)

    def test_ambiguous_episode_summary_does_not_assign_to_either_show(self):
        summary = {"Title Name": "Episode", "Seconds Watched": "1800",
                   "Most Recent Watch Date": "2022-01-01T13:00:00Z"}
        parsed = parse_amazon_zip(archive([session(seconds=1), session(title="Episode-Other - Season 1", seconds=1)], [summary]))
        self.assertEqual(parsed.rows, [])

    def test_hyphens_inside_episode_names_and_localized_seasons(self):
        self.assertEqual(_identity("The Instant White-Hot Wild-The Boys - Season 3"),
                         ("The Instant White-Hot Wild", "The Boys", 3))
        self.assertEqual(_identity("Title-Lucifer - Staffel 4 [dt./OV]"), ("Title", "Lucifer", 4))

    def test_standalone_summary_preserves_hyphenated_series_and_episode_names(self):
        for episode, show in (("Pilot", "Spider-Man"), ("White-Hot Wild", "The Boys"),
                              ("White-Hot Wild", "Spider-Man")):
            with self.subTest(episode=episode, show=show):
                summary = {"Title Name": episode, "Seconds Watched": "1800",
                           "Most Recent Watch Date": "2022-01-01T13:00:00Z"}
                parsed = parse_amazon_zip(archive([session(title=f"{episode}-{show} - Season 1")], [summary]))
                self.assertEqual(len(parsed.rows), 1)
                self.assertEqual(parsed.rows[0].episode_title, episode)
                self.assertEqual(parsed.rows[0].show_title, show)
                self.assertEqual(parsed.duplicate_rows, 1)

    def test_invalid_archive_and_missing_history_rejected(self):
        with self.assertRaises(ValueError): parse_amazon_zip(b"bad zip")
        out = io.BytesIO()
        with ZipFile(out, "w") as z: z.writestr("Watchlist.csv", "Title\nMovie")
        with self.assertRaises(ValueError): parse_amazon_zip(out.getvalue())

    def test_bad_date_is_reported_without_losing_good_observations(self):
        parsed = parse_amazon_zip(archive([session(), session(**{"Playback Start Datetime (UTC)": "bad"})]))
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(len(parsed.errors), 1)

    def test_duplicated_movie_title_is_normalized_before_matching(self):
        for title in ("Movie", "Spider-Man", "Spider-Man: Far From Home"):
            with self.subTest(title=title):
                parsed = parse_amazon_zip(archive([session(title=f"{title}-{title}")]))
                self.assertEqual(parsed.rows[0].source_title, title)

    def test_export_locale_is_inferred_and_explicit_choice_wins(self):
        data = archive([session(**{"Preferred Language Code": '"de"'})])
        self.assertEqual(parse_amazon_zip(data).language, "de")
        self.assertEqual(parse_amazon_zip(data, language="en").language, "en")
