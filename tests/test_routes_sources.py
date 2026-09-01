import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from anachron.routes import load_contract
from anachron.routes.sources import (
    SourceDiscoveryError,
    SourceIneligibleError,
    UpstreamFetchReceipt,
    build_sampling_frame,
    discover_topic,
    main,
    validate_exante_sampling_frame,
    write_discovery_artifact,
    write_sampling_frame,
)

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "research" / "routes-v1" / "contract.json"
FIXTURES = Path(__file__).parent / "fixtures" / "routes"


class TestRoutesSources(unittest.TestCase):
    def setUp(self):
        self.contract = load_contract(CONTRACT_PATH)
        self.strict_raw = (FIXTURES / "strict_revision.json").read_bytes()
        self.post_raw = (FIXTURES / "post_revisions.json").read_bytes()
        self.huggingface_resolved_url = (
            "https://huggingface.co/api/resolve-cache/datasets/yachuanliu/ExAnte/"
            "4e30593e1aff7360fef5aee865117c5c8e05114e/exante_wiki.csv"
            "?download=true&etag=%2219d247884a11aa8a8f6359fdb841643219703e72%22"
        )
        self.frame = {
            "schema_version": "routes-v1-exante-sampling-frame",
            "github_revision": self.contract["upstreams"]["exante_github"]["revision"],
            "github_artifact_url": self.contract["upstreams"]["exante_github"]["artifact_url"],
            "github_source_sha256": "sha256:" + "a" * 64,
            "huggingface_revision": self.contract["upstreams"]["exante_huggingface"]["revision"],
            "huggingface_artifact_url": self.contract["upstreams"]["exante_huggingface"]["artifact_url"],
            "huggingface_resolved_url": self.huggingface_resolved_url,
            "huggingface_etag": '"19d247884a11aa8a8f6359fdb841643219703e72"',
            "huggingface_source_sha256": "sha256:" + "b" * 64,
            "observed_row_count": 60,
            "observed_unique_pair_count": 60,
            "topics": [
                topic
                for group in self.contract["sampling"]["topics"].values()
                for topic in group
            ],
        }

    def _fetcher(self, url, timeout_seconds):
        self.assertEqual(timeout_seconds, 30.0)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["titles"], ["YouTube"])
        self.assertEqual(query["rvdir"], ["older"])
        self.assertEqual(query["rvlimit"], ["1"])
        self.assertEqual(query["maxlag"], ["5"])
        if query["rvstart"] == ["2013-12-31T23:59:59Z"]:
            return self.strict_raw
        if query["rvstart"] == ["2014-12-31T23:59:59Z"]:
            return self.post_raw
        self.fail(f"unexpected MediaWiki start: {query['rvstart']}")

    def test_sampling_frame_requires_exact_pins_hashes_and_title_year_pairs(self):
        validate_exante_sampling_frame(self.contract, self.frame)
        changed_revision = copy.deepcopy(self.frame)
        changed_revision["github_revision"] = "0" * 40
        with self.assertRaises(SourceDiscoveryError):
            validate_exante_sampling_frame(self.contract, changed_revision)
        changed_title = copy.deepcopy(self.frame)
        changed_title["topics"][0]["cutoff_year"] = 2014
        with self.assertRaises(SourceDiscoveryError):
            validate_exante_sampling_frame(self.contract, changed_title)
        invalid_hash = copy.deepcopy(self.frame)
        invalid_hash["github_source_sha256"] = "sha256:bad"
        with self.assertRaises(SourceDiscoveryError):
            validate_exante_sampling_frame(self.contract, invalid_hash)

    def test_default_mediawiki_fetch_uses_declared_user_agent_and_never_retries(self):
        from anachron.routes.sources import _default_fetch_bytes

        url = "https://en.wikipedia.org/w/api.php?action=query"
        with patch("anachron.routes.sources.urlopen") as mock_urlopen:
            response = mock_urlopen.return_value.__enter__.return_value
            response.status = 200
            response.geturl.return_value = url
            response.read.return_value = b"{}"
            self.assertEqual(_default_fetch_bytes(url, 30.0), b"{}")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("User-agent"),
            "AnachronRoutes/0.1 (https://github.com/LesterALeong/anachron)",
        )

        for status in (429, 503):
            with self.subTest(status=status):
                http_error = HTTPError(url, status, "HTTP error", None, None)
                with patch(
                    "anachron.routes.sources.urlopen", side_effect=http_error
                ) as mock_urlopen, self.assertRaises(SourceDiscoveryError):
                    _default_fetch_bytes(url, 30.0)
                self.assertEqual(mock_urlopen.call_count, 1)

    def test_discovery_emits_fixed_horizon_snapshot_metadata_hashes_and_diff(self):
        artifact = discover_topic(
            self.contract,
            self.frame,
            phase="pilot",
            title="YouTube",
            fetcher=self._fetcher,
        )
        self.assertEqual(artifact.strict_revision.revision_id, 101)
        self.assertEqual(
            artifact.strict_revision.raw_response_sha256,
            "sha256:" + hashlib.sha256(self.strict_raw).hexdigest(),
        )
        self.assertEqual(
            artifact.strict_revision.content_sha256,
            "sha256:" + hashlib.sha256(b"Before revision\n").hexdigest(),
        )
        self.assertEqual(artifact.post_snapshot_horizon_days, 365)
        self.assertEqual(artifact.post_snapshot.revision_id, 103)
        document = artifact.to_dict()
        self.assertIn("--- oldid:101\n+++ oldid:103\n", document["snapshot_diff"])
        self.assertIn("-Before revision", document["snapshot_diff"])
        self.assertNotIn("answer_label", document)

    def test_discovery_rejects_redirect_title_hash_and_boundary_failures(self):
        redirected = json.loads(self.strict_raw)
        redirected["query"]["redirects"] = [{"from": "YT", "to": "YouTube"}]
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda _url, _timeout: json.dumps(redirected).encode(),
            )
        redirect_page = json.loads(self.strict_raw)
        redirect_page["query"]["pages"][0]["redirect"] = ""
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda _url, _timeout: json.dumps(redirect_page).encode(),
            )
        bad_sha = json.loads(self.strict_raw)
        bad_sha["query"]["pages"][0]["revisions"][0]["sha1"] = "bad"
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda _url, _timeout: json.dumps(bad_sha).encode(),
            )
        base36_sha = json.loads(self.strict_raw)
        base36_sha["query"]["pages"][0]["revisions"][0]["sha1"] = (
            "4gmfn6hfxi6zrdgdt18qbqto4nxqhxq"
        )
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda _url, _timeout: json.dumps(base36_sha).encode(),
            )
        ineligible_post = json.loads(self.post_raw)
        ineligible_post["query"]["pages"][0]["revisions"][0]["timestamp"] = "2013-12-31T23:59:59Z"
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda url, _timeout: self.strict_raw if "2013-12-31" in url else json.dumps(ineligible_post).encode(),
            )

    def test_discovery_distinguishes_ineligible_source_from_external_failure(self):
        no_strict_revision = json.loads(self.strict_raw)
        no_strict_revision["query"]["pages"][0]["revisions"] = []
        with self.assertRaises(SourceIneligibleError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda url, _timeout: json.dumps(no_strict_revision).encode()
                if "2013-12-31" in url
                else self.post_raw,
            )
        no_post_revision = json.loads(self.post_raw)
        no_post_revision["query"]["pages"][0]["revisions"] = []
        with self.assertRaises(SourceIneligibleError):
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda url, _timeout: self.strict_raw
                if "2013-12-31" in url
                else json.dumps(no_post_revision).encode(),
            )
        with self.assertRaises(SourceDiscoveryError) as captured:
            discover_topic(
                self.contract,
                self.frame,
                phase="pilot",
                title="YouTube",
                fetcher=lambda _url, _timeout: b"not JSON",
            )
        self.assertNotIsInstance(captured.exception, SourceIneligibleError)

    def test_ineligible_source_remains_a_nonzero_cli_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            frame_path = Path(directory) / "sampling_frame.json"
            write_sampling_frame(frame_path, self.frame)
            with patch(
                "anachron.routes.sources.discover_topic",
                side_effect=SourceIneligibleError("no admissible revision pair"),
            ), self.assertRaises(SystemExit) as captured:
                main(
                    [
                        "discover",
                        "--contract",
                        str(CONTRACT_PATH),
                        "--sampling-frame",
                        str(frame_path),
                        "--phase",
                        "pilot",
                        "--title",
                        "YouTube",
                        "--output",
                        str(Path(directory) / "unused.json"),
                    ]
                )
        self.assertNotEqual(captured.exception.code, 0)
        self.assertEqual(
            captured.exception.code,
            "source discovery failed: no admissible revision pair",
        )

    def test_full_phase_excludes_pilot_titles_and_artifacts_are_deterministic_utf8(self):
        with self.assertRaises(SourceDiscoveryError):
            discover_topic(
                self.contract,
                self.frame,
                phase="full",
                title="YouTube",
                fetcher=self._fetcher,
            )
        artifact = discover_topic(
            self.contract,
            self.frame,
            phase="pilot",
            title="YouTube",
            fetcher=self._fetcher,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            write_discovery_artifact(output, artifact)
            first = output.read_bytes()
            write_discovery_artifact(output, artifact)
            self.assertEqual(first, output.read_bytes())
            self.assertTrue(first.endswith(b"\n"))
            self.assertEqual(json.loads(first.decode("utf-8"))["title"], "YouTube")

    def test_build_frame_fetches_exact_pins_and_proves_all_title_year_pairs(self):
        csv_rows = ["Title,Cutoff_Year"]
        csv_rows.extend(
            f"{topic['title']},{topic['cutoff_year']}"
            for topic in self.frame["topics"]
        )
        github_raw = b"# ExAnte wiki README\n"
        csv_raw = ("\n".join(csv_rows) + "\n").encode("utf-8")

        def fetcher(url, timeout_seconds):
            self.assertEqual(timeout_seconds, 30.0)
            if url == self.contract["upstreams"]["exante_github"]["artifact_url"]:
                return UpstreamFetchReceipt(body=github_raw, resolved_url=url)
            if url == self.contract["upstreams"]["exante_huggingface"]["artifact_url"]:
                return UpstreamFetchReceipt(
                    body=csv_raw, resolved_url=self.huggingface_resolved_url
                )
            self.fail(f"unexpected pinned artifact URL: {url}")

        frame = build_sampling_frame(self.contract, fetcher=fetcher)
        validate_exante_sampling_frame(self.contract, frame)
        self.assertEqual(frame["observed_row_count"], 60)
        self.assertEqual(frame["observed_unique_pair_count"], 60)
        self.assertEqual(frame["huggingface_resolved_url"], self.huggingface_resolved_url)
        self.assertEqual(
            frame["huggingface_etag"], '"19d247884a11aa8a8f6359fdb841643219703e72"'
        )
        self.assertEqual(
            frame["github_source_sha256"],
            "sha256:" + hashlib.sha256(github_raw).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sampling_frame.json"
            write_sampling_frame(output, frame)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), frame)
        bad_header = csv_raw.replace(b"Title,Cutoff_Year", b"title,year", 1)
        with self.assertRaises(SourceDiscoveryError):
            build_sampling_frame(
                self.contract,
                fetcher=lambda url, _timeout: UpstreamFetchReceipt(
                    body=github_raw if url == self.contract["upstreams"]["exante_github"]["artifact_url"] else bad_header,
                    resolved_url=url
                    if url == self.contract["upstreams"]["exante_github"]["artifact_url"]
                    else self.huggingface_resolved_url,
                ),
            )

    def test_frame_builder_rejects_every_noncanonical_huggingface_redirect(self):
        csv_rows = ["Title,Cutoff_Year"]
        csv_rows.extend(
            f"{topic['title']},{topic['cutoff_year']}"
            for topic in self.frame["topics"]
        )
        csv_raw = ("\n".join(csv_rows) + "\n").encode("utf-8")
        github_url = self.contract["upstreams"]["exante_github"]["artifact_url"]
        bad_urls = (
            self.huggingface_resolved_url.replace("huggingface.co", "example.com", 1),
            self.huggingface_resolved_url.replace(
                "4e30593e1aff7360fef5aee865117c5c8e05114e", "0" * 40, 1
            ),
            self.huggingface_resolved_url.replace("exante_wiki.csv", "other.csv", 1),
            self.huggingface_resolved_url.split("?")[0],
        )
        for bad_url in bad_urls:
            with self.subTest(bad_url=bad_url), self.assertRaises(SourceDiscoveryError):
                build_sampling_frame(
                    self.contract,
                    fetcher=lambda url, _timeout, bad_url=bad_url: UpstreamFetchReceipt(
                        body=b"# README\n" if url == github_url else csv_raw,
                        resolved_url=url if url == github_url else bad_url,
                    ),
                )


if __name__ == "__main__":
    unittest.main()
