"""
Verification script for live INTS Voting service running on port 8081.
Tests:
1. Health endpoint
2. Root redirect to /results
3. Public /results has no secret /vote link
4. Public /register has no secret /vote link
5. Public /admin/login has no secret /vote link
6. 404 error page has no secret /vote link for unauthenticated user
7. Admin dashboard (/admin) contains all 5 system screens
8. Admin voters audit (/admin/voters) contains all 5 system screens
9. Real-time /api/check-voter API
10. Session authentication cookie verification
"""

import sys
import json
import http.cookiejar
import urllib.request
import urllib.parse

BASE_URL = "http://localhost:8081"
ADMIN_PASS = "Ints@Halloween2026!"

def test_health():
    req = urllib.request.urlopen(f"{BASE_URL}/health")
    assert req.status == 200, f"Health returned {req.status}"
    data = json.loads(req.read().decode("utf-8"))
    assert data.get("status") == "ok", f"Health data: {data}"
    print("[PASS] 1. Health endpoint")

def test_root_redirect():
    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, headers):
            return fp
        http_error_301 = http_error_302
        http_error_303 = http_error_302
        http_error_307 = http_error_302

    opener = urllib.request.build_opener(NoRedirectHandler)
    res = opener.open(f"{BASE_URL}/")
    assert res.status in (301, 302, 303, 307), f"Expected redirect, got {res.status}"
    loc = res.headers.get("Location", "")
    assert "/results" in loc, f"Expected redirect to /results, got {loc}"
    print(f"[PASS] 2. Root redirect to /results ({loc})")

def test_secrecy():
    pages = ["/results", "/register", "/admin/login", "/nonexistent_404_page"]
    for path in pages:
        try:
            req = urllib.request.urlopen(f"{BASE_URL}{path}")
            html = req.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            html = exc.read().decode("utf-8")

        assert 'href="/vote"' not in html, f"Secret /vote link leaked in {path}"
        assert "url_for('public.vote')" not in html, f"Template code leaked in {path}"
        print(f"[PASS] 3. Secrecy: no /vote link in {path}")

def test_moderator_views():
    req_headers = {"X-Admin-Password": ADMIN_PASS}
    five_screens = [
        "/admin",
        "/vote",
        "/register",
        "/results",
        "/admin/voters"
    ]

    for view in ["/admin", "/admin/voters"]:
        req = urllib.request.Request(f"{BASE_URL}{view}", headers=req_headers)
        res = urllib.request.urlopen(req)
        assert res.status == 200, f"Expected 200 for {view}, got {res.status}"
        html = res.read().decode("utf-8")

        for screen in five_screens:
            assert screen in html, f"Missing link {screen} in {view}"
        print(f"[PASS] 4. Moderator view {view} contains all 5 system screens")

def test_api_check_voter():
    # Invalid domain
    req = urllib.request.urlopen(f"{BASE_URL}/api/check-voter?email=test@gmail.com")
    data = json.loads(req.read().decode("utf-8"))
    assert not data.get("valid"), f"Expected invalid domain: {data}"

    # Valid domain
    req = urllib.request.urlopen(f"{BASE_URL}/api/check-voter?email=test.valid@ints.org.br")
    data = json.loads(req.read().decode("utf-8"))
    assert data.get("valid"), f"Expected valid domain: {data}"
    assert not data.get("has_voted"), f"Expected has_voted False: {data}"
    print("[PASS] 5. Real-time /api/check-voter API")

def test_browser_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    login_data = urllib.parse.urlencode({"password": ADMIN_PASS}).encode("utf-8")
    req_login = urllib.request.Request(f"{BASE_URL}/admin/login", data=login_data)
    res_login = opener.open(req_login)
    assert res_login.status in (200, 302, 303)

    # Session access to /admin
    res_admin = opener.open(f"{BASE_URL}/admin")
    assert res_admin.status == 200
    html_admin = res_admin.read().decode("utf-8")
    assert "Modo Administrador Ativo" in html_admin

    # Session access to /vote
    res_vote = opener.open(f"{BASE_URL}/vote")
    assert res_vote.status == 200
    html_vote = res_vote.read().decode("utf-8")
    assert "Painel" in html_vote  # Moderator nav is rendered

    print("[PASS] 6. Browser session cookie persistence and moderator nav")

if __name__ == "__main__":
    test_health()
    test_root_redirect()
    test_secrecy()
    test_moderator_views()
    test_api_check_voter()
    test_browser_session()
    print("\n==========================================")
    print("ALL 6 LIVE VPS VERIFICATION SUITES PASSED!")
    print("==========================================")
