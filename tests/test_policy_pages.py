def test_public_policy_pages_are_available():
    from app.main import data_deletion_instructions, privacy_policy, terms_of_service

    privacy = privacy_policy().body.decode("utf-8")
    deletion = data_deletion_instructions().body.decode("utf-8")
    terms = terms_of_service().body.decode("utf-8")

    assert "Privacy Policy" in privacy
    assert "encrypted access token" in privacy
    assert "User Data Deletion" in deletion
    assert "within 30 days" in deletion
    assert "Terms of Service" in terms
