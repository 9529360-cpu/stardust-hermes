"""F7.1 — single, bounded, cached AWS credential probe.

Regression tests for ``agent/bedrock_adapter.py`` credential detection:

- ``has_aws_credentials`` runs the boto3 chain at most once per call (was twice).
- The chain result is cached per process and invalidated by AWS_* env changes.
- Env-var credentials are detected with zero network wait.
- IMDS-sourced credentials are still detected via a local stub IMDSv2 server
  (``AWS_EC2_METADATA_SERVICE_ENDPOINT``); no real AWS is ever contacted.
"""

import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from agent import bedrock_adapter as ba


@pytest.fixture(autouse=True)
def _reset_chain_cache():
    ba.reset_aws_credential_chain_cache()
    yield
    ba.reset_aws_credential_chain_cache()


class _ChainCounter:
    """Count real chain executions by wrapping the uncached runner."""

    def __init__(self):
        self.count = 0
        self._orig = ba._run_boto3_chain

    def __enter__(self):
        ba._run_boto3_chain = self._wrapped
        return self

    def _wrapped(self, env):
        self.count += 1
        return self._orig(env)

    def __exit__(self, *exc):
        ba._run_boto3_chain = self._orig
        return False


class TestChainRunsAtMostOnce:
    def test_has_aws_credentials_single_chain_run(self):
        with _ChainCounter() as counter:
            assert ba.has_aws_credentials({}) in (True, False)
        assert counter.count == 1

    def test_second_call_uses_cache(self):
        with _ChainCounter() as counter:
            ba.has_aws_credentials({})
            ba.has_aws_credentials({})
            ba.resolve_aws_auth_env_var({})
        assert counter.count == 1

    def test_env_var_change_invalidates_cache(self, monkeypatch):
        # conftest already sets AWS_EC2_METADATA_DISABLED; use a var nothing else sets.
        with _ChainCounter() as counter:
            ba.has_aws_credentials({})            # fingerprint A -> chain run 1
            monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIACHANGESFINGERPRINT")
            ba.has_aws_credentials({})            # fingerprint B -> chain re-run
        assert counter.count == 2


class TestEnvVarCredentials:
    def test_key_pair_detected_without_network(self):
        env = {"AWS_ACCESS_KEY_ID": "AKIADUMMY", "AWS_SECRET_ACCESS_KEY": "dummy"}
        with _ChainCounter() as counter:
            assert ba.has_aws_credentials(env) is True
        assert counter.count == 0

    def test_profile_detected_without_network(self, tmp_path):
        creds = tmp_path / "credentials"
        creds.write_text("[default]\naws_access_key_id = AKIADUMMY\naws_secret_access_key = dummy\n")
        env = {"AWS_PROFILE": "default", "AWS_SHARED_CREDENTIALS_FILE": str(creds)}
        with _ChainCounter() as counter:
            assert ba.has_aws_credentials(env) is True
        assert counter.count == 0

    def test_bearer_token_detected_without_network(self):
        env = {"AWS_BEARER_TOKEN_BEDROCK": "token"}
        with _ChainCounter() as counter:
            assert ba.has_aws_credentials(env) is True
        assert counter.count == 0


class TestNoCredentials:
    def test_no_credentials_false_without_aws_contact(self, monkeypatch):
        # Any outbound attempt to the real metadata IP must fail loudly, not hang.
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", "http://127.0.0.1:9/nothing")
        t0 = time.perf_counter()
        assert ba.has_aws_credentials() is False
        assert time.perf_counter() - t0 < 5.0


class TestStubImds:
    """IMDSv2 stub: a local HTTP server answering token + role + credentials."""

    @pytest.fixture
    def stub_imds(self, monkeypatch):
        # conftest sets AWS_EC2_METADATA_DISABLED=true for the whole suite; the stub
        # tests exercise the real IMDS path, so opt this fixture out.
        monkeypatch.delenv("AWS_EC2_METADATA_DISABLED", raising=False)
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _respond(self, body, status=200):
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PUT(self):
                requests.append(("PUT", self.path))
                self._respond("stub-imds-token")

            def do_GET(self):
                requests.append(("GET", self.path))
                if self.path == "/latest/meta-data/iam/security-credentials/":
                    self._respond("stub-role")
                elif self.path == "/latest/meta-data/iam/security-credentials/stub-role":
                    self._respond(json.dumps({
                        "AccessKeyId": "ASIASTUBSTUBSTUB",
                        "SecretAccessKey": "stub-secret",
                        "Token": "stub-token",
                        "Expiration": "2030-01-01T00:00:00Z",
                    }))
                else:
                    self._respond("not found", status=404)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}", requests
        server.shutdown()
        server.server_close()

    def test_imds_credentials_detected(self, stub_imds, monkeypatch):
        endpoint, requests = stub_imds
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", endpoint)
        t0 = time.perf_counter()
        assert ba.has_aws_credentials() is True
        assert time.perf_counter() - t0 < 5.0
        paths = [p for _, p in requests]
        assert "/latest/api/token" in paths
        assert ba.resolve_aws_auth_env_var() == "iam-role"

    def test_imds_negative_result_cached(self, stub_imds, monkeypatch):
        # Stub answers the token but a role list that 404s -> False, then cached.
        endpoint, requests = stub_imds
        monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", endpoint + "/empty")
        with _ChainCounter() as counter:
            assert ba.has_aws_credentials() is False
            assert ba.has_aws_credentials() is False
        assert counter.count == 1


class TestCacheBoundaries:
    def test_reset_function_clears_cache(self):
        ba._chain_cache_key = "forced"
        ba._chain_cache_value = True
        ba.reset_aws_credential_chain_cache()
        assert ba._chain_cache_key is None

    def test_fingerprint_changes_with_env_and_files(self, tmp_path):
        env1 = {"AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "credentials")}
        key1 = ba._aws_chain_fingerprint(env1)
        assert ba._aws_chain_fingerprint({}) != key1
        creds = tmp_path / "credentials"
        creds.write_text("[default]\naws_access_key_id=x\n")
        key2 = ba._aws_chain_fingerprint(env1)
        assert key2 != key1  # file appearing changes the fingerprint
