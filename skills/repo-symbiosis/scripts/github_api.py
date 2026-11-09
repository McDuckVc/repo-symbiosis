"""Repository-scoped GitHub App authentication and fast-forward ledger writes."""

import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import delivery, github_gate, privacy
from .repository import SymbiosisError, json_bytes, require


class APIError(SymbiosisError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub REST request failed ({status})")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(method, path, token, body=None):
    require(path.startswith("/") and not path.startswith("//"), "Expected a GitHub API path")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
    }
    query = Request("https://api.github.com" + path, data=json_bytes(body) if body is not None else None,
                    headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(query, timeout=12) as response:
            data = response.read(16 * 1024 * 1024 + 1)
        require(len(data) <= 16 * 1024 * 1024, "GitHub response exceeded its size limit")
        return json.loads(data) if data else None
    except HTTPError as error:
        raise APIError(error.code) from None
    except (URLError, TimeoutError):
        raise APIError(503) from None


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def app_jwt(app_id, key):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    now = int(time.time())
    signer = serialization.load_pem_private_key(key, password=None)
    require(isinstance(signer, rsa.RSAPrivateKey), "Use the GitHub App RSA private key")
    payload = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    message = b64url(b'{"alg":"RS256","typ":"JWT"}') + b"." + b64url(json_bytes(payload))
    return (message + b"." + b64url(signer.sign(message, padding.PKCS1v15(), hashes.SHA256()))).decode("ascii")


class GitHub:
    def __init__(self, app_id, key, job):
        installation = job["installation"]
        result = request("POST", f"/app/installations/{installation}/access_tokens", app_jwt(app_id, key), {
            "repository_ids": [job["repo_id"]],
            "permissions": {"contents": "write", "issues": "read", "pull_requests": "read", "deployments": "read"},
        })
        self.token = result["token"]
        self.prefix = "/repos/" + job["repo"]

    def call(self, method, path, body=None):
        return request(method, self.prefix + path, self.token, body)

    def ref(self, branch, missing=False):
        try:
            value = self.call("GET", "/git/ref/heads/" + quote(branch, safe="/"))
        except APIError as error:
            if missing and error.status == 404:
                return None
            raise
        require(value["ref"] == f"refs/heads/{branch}" and value["object"]["type"] == "commit",
                "GitHub returned a different branch")
        return github_gate.oid(value["object"]["sha"])

    def file(self, path, revision):
        value = self.call("GET", "/contents/" + quote(path, safe="/") + "?" + urlencode({"ref": revision}))
        require(isinstance(value, dict) and value.get("type") == "file" and value.get("encoding") == "base64",
                f"GitHub returned an unexpected file for {path}")
        return base64.b64decode(value["content"])

    def contains(self, before, after):
        if before == after:
            return True
        value = self.call("GET", f"/compare/{before}...{after}?per_page=1")
        return value["status"] in ("ahead", "identical")

    def user(self, login):
        value = self.call("GET", f"/collaborators/{quote(login, safe='')}/permission")
        return {"id": value["user"]["id"], "login": value["user"]["login"],
                "permission": value["permission"], "role": value["role_name"]}

    def ledger(self):
        branch = delivery.REF.removeprefix("refs/heads/")
        head = self.ref(branch, missing=True)
        if head is None:
            return None, {"schema": 1, "claims": {}, "completed": {}}
        return head, delivery.decode_state(self.file("claims.json", head))

    def update(self, change, message):
        branch = delivery.REF.removeprefix("refs/heads/")
        for _ in range(6):
            old, state = self.ledger()
            if not change(state):
                return
            delivery.prune_receipts(state)
            content = json_bytes(state)
            require(len(content) <= 900 * 1024, "Coordination ledger reached the REST-backed preview size limit")
            delivery.decode_state(content)
            privacy.reject(privacy.inspect("claims.json", content))
            tree = self.call("POST", "/git/trees", {"tree": [
                {"path": "claims.json", "mode": "100644", "type": "blob", "content": content.decode("ascii")},
            ]})["sha"]
            commit = self.call("POST", "/git/commits", {
                "message": message, "tree": tree, "parents": [old] if old else [],
            })["sha"]
            try:
                if old:
                    self.call("PATCH", f"/git/refs/heads/{branch}", {"sha": commit, "force": False})
                else:
                    self.call("POST", "/git/refs", {"ref": delivery.REF, "sha": commit})
                return
            except APIError as error:
                if error.status not in (409, 422):
                    raise
                latest = self.ref(branch, missing=True)
                if latest is None or latest == old:
                    raise
                require(old is None or self.contains(old, latest), "Coordination history was rewritten")
        raise APIError(409)
